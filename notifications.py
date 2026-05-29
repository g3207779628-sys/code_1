"""6 通道通知 + 预警扫描。

调用入口：
- notify(channel_code, recipient, subject, body, payload=None) -> (ok, detail)
- scan_low_stock_and_alert() / scan_damage_pending_and_alert()
- run_all_scans() —— APScheduler 每日定时跑

外部渠道要填的凭证 (config_json 里的 key)：
- email:       smtp_server, smtp_port, smtp_user, smtp_password, from_addr, use_ssl, default_recipient
- sms:         access_key_id, access_key_secret, sign_name, template_code, region, default_recipient (手机号)
- wechat_work: corpid, corpsecret, agentid, default_recipient (@all 或 userid|userid)
- qq_bot:      base_url (OneBot HTTP 实例), access_token, default_recipient (group:xxx / user:xxx)
- wps:         webhook_url, format (markdown / text), default_recipient (兼容钉钉/企微/飞书 群机器人)
"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import requests

import database

# 发外部通知统一用这个会话：trust_env=False 表示不读系统/环境代理。
# 原因：Windows 上 requests 会自动套用「系统代理」(注册表 WinINET，常被 Clash 等
# 代理软件的"系统代理"开关写入)，把飞书/企微/阿里云短信等【国内】服务的请求也送去
# 国外节点，导致 TLS 握手被中断 (SSL: UNEXPECTED_EOF_WHILE_READING)。这些都是国内
# 服务、本就该直连，故统一绕过环境代理。
_HTTP = requests.Session()
_HTTP.trust_env = False


def _load_config(channel_code):
    with database.get_conn() as conn:
        row = conn.execute(
            "SELECT enabled, config_json FROM notification_channel WHERE code = ?",
            (channel_code,),
        ).fetchone()
    if not row:
        return None, {}
    try:
        cfg = json.loads(row["config_json"] or "{}")
    except json.JSONDecodeError:
        cfg = {}
    return bool(row["enabled"]), cfg


def notify(channel_code, recipient, subject, body, payload=None):
    """统一入口。返回 (ok: bool, detail: str)。"""
    enabled, cfg = _load_config(channel_code)
    if enabled is None:
        return False, f"未知渠道 {channel_code}"
    if not enabled:
        return False, f"渠道 {channel_code} 未启用"
    sender = _DISPATCH.get(channel_code)
    if not sender:
        return False, f"无 {channel_code} 实现"
    try:
        return sender(cfg, recipient or cfg.get("default_recipient", ""), subject, body, payload or {})
    except Exception as e:
        return False, f"{channel_code} 异常：{type(e).__name__}: {e}"


# ---------- 站内 ----------

def _send_inapp(cfg, recipient, subject, body, payload):
    # 事件已被 _trigger_rule 写入 alert_event；这里仅返回成功
    return True, "已写入 alert_event"


# ---------- 邮件 ----------

def _send_email(cfg, recipient, subject, body, payload):
    import smtplib
    from email.mime.text import MIMEText

    server = cfg.get("smtp_server")
    port = int(cfg.get("smtp_port", 465) or 465)
    user = cfg.get("smtp_user")
    pwd = cfg.get("smtp_password")
    from_addr = cfg.get("from_addr") or user
    if not (server and user and pwd):
        return False, "邮件配置不完整：需要 smtp_server / smtp_user / smtp_password"
    if not recipient:
        return False, "未指定收件人"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = recipient

    if str(cfg.get("use_ssl", "1")) in ("1", "true", "True", "on"):
        smtp = smtplib.SMTP_SSL(server, port, timeout=15)
    else:
        smtp = smtplib.SMTP(server, port, timeout=15)
        smtp.starttls()
    smtp.login(user, pwd)
    smtp.sendmail(from_addr, [r.strip() for r in recipient.split(",") if r.strip()], msg.as_string())
    smtp.quit()
    return True, f"邮件已投递到 {recipient}"


# ---------- 阿里云短信（v2 RPC 签名） ----------

def _aliyun_sign(params, secret):
    items = sorted(params.items())
    canonical = "&".join(f"{quote(k, safe='~')}={quote(str(v), safe='~')}" for k, v in items)
    string_to_sign = "POST&%2F&" + quote(canonical, safe="~")
    h = hmac.new((secret + "&").encode(), string_to_sign.encode(), hashlib.sha1)
    return base64.b64encode(h.digest()).decode()


def _send_sms(cfg, recipient, subject, body, payload):
    ak = cfg.get("access_key_id")
    sk = cfg.get("access_key_secret")
    sign_name = cfg.get("sign_name")
    template_code = cfg.get("template_code")
    if not (ak and sk and sign_name and template_code):
        return False, "短信配置缺：access_key_id / access_key_secret / sign_name / template_code"
    if not recipient:
        return False, "未指定手机号"

    template_param = payload.get("template_param") or {"content": body[:60]}
    params = {
        "Action": "SendSms",
        "Version": "2017-05-25",
        "Format": "JSON",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureMethod": "HMAC-SHA1",
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
        "AccessKeyId": ak,
        "RegionId": cfg.get("region", "cn-hangzhou"),
        "PhoneNumbers": recipient,
        "SignName": sign_name,
        "TemplateCode": template_code,
        "TemplateParam": json.dumps(template_param, ensure_ascii=False),
    }
    params["Signature"] = _aliyun_sign(params, sk)
    resp = _HTTP.post("https://dysmsapi.aliyuncs.com/", data=params, timeout=15)
    try:
        j = resp.json()
    except ValueError:
        return False, f"短信网关返回非 JSON：{resp.text[:200]}"
    if j.get("Code") == "OK":
        return True, f"短信下发 BizId={j.get('BizId', '')}"
    return False, f"短信失败：{j.get('Code')} {j.get('Message', '')}"


# ---------- 企业微信 ----------

_WECHAT_TOKEN_CACHE = {}

def _wechat_get_token(corpid, corpsecret):
    cache_key = f"{corpid}|{corpsecret[:6]}"
    item = _WECHAT_TOKEN_CACHE.get(cache_key)
    if item and time.time() < item["exp"] - 60:
        return item["token"]
    r = _HTTP.get(
        "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
        params={"corpid": corpid, "corpsecret": corpsecret},
        timeout=10,
    ).json()
    if r.get("errcode") != 0:
        raise RuntimeError(f"获取 access_token 失败：{r}")
    _WECHAT_TOKEN_CACHE[cache_key] = {"token": r["access_token"], "exp": time.time() + int(r.get("expires_in", 7200))}
    return r["access_token"]


def _send_wechat_work(cfg, recipient, subject, body, payload):
    corpid = cfg.get("corpid")
    corpsecret = cfg.get("corpsecret")
    agentid = cfg.get("agentid")
    if not (corpid and corpsecret and agentid):
        return False, "企业微信配置缺：corpid / corpsecret / agentid"
    token = _wechat_get_token(corpid, corpsecret)
    data = {
        "touser": recipient or "@all",
        "msgtype": "text",
        "agentid": int(agentid),
        "text": {"content": f"【{subject}】\n{body}"},
        "safe": 0,
    }
    r = _HTTP.post(
        f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
        json=data, timeout=10,
    ).json()
    if r.get("errcode") == 0:
        return True, "企业微信已发送"
    return False, f"企业微信失败：errcode={r.get('errcode')} {r.get('errmsg', '')}"


# ---------- QQ 机器人（OneBot v11 HTTP） ----------

def _send_qq_bot(cfg, recipient, subject, body, payload):
    base = cfg.get("base_url")
    token = cfg.get("access_token", "")
    if not base:
        return False, "QQ 机器人配置缺：base_url（OneBot v11 HTTP 实例，如 NapCat / Lagrange）"
    if not recipient:
        return False, "recipient 未指定，需要 group:xxx 或 user:xxx"
    if recipient.startswith("group:"):
        endpoint = "/send_group_msg"
        data = {"group_id": int(recipient[6:]), "message": f"【{subject}】\n{body}"}
    elif recipient.startswith("user:"):
        endpoint = "/send_private_msg"
        data = {"user_id": int(recipient[5:]), "message": f"【{subject}】\n{body}"}
    else:
        return False, "recipient 需要 group:xxx 或 user:xxx 格式"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = _HTTP.post(f"{base.rstrip('/')}{endpoint}", json=data, headers=headers, timeout=10).json()
    if r.get("status") == "ok" or r.get("retcode") == 0:
        return True, "QQ 消息已发送"
    return False, f"QQ 失败：{r}"


# ---------- 群机器人 Webhook ----------

def _post_webhook(url, data):
    r = _HTTP.post(url, json=data, timeout=10)
    try:
        rj = r.json()
    except ValueError:
        return False, f"返回非 JSON：{r.text[:200]}"
    if "code" in rj:
        if rj.get("code") == 0:
            return True, "已发送"
        return False, f"失败：{rj}"
    if "errcode" in rj:
        if rj.get("errcode") == 0:
            return True, "已发送"
        return False, f"失败：{rj}"
    if "StatusCode" in rj:
        if rj.get("StatusCode") == 0:
            return True, "已发送"
        return False, f"失败：{rj}"
    return False, f"失败：{rj}"


def _feishu_sign(secret):
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    sign = base64.b64encode(
        hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    ).decode("utf-8")
    return timestamp, sign


def _apply_feishu_auth(data, cfg):
    secret = cfg.get("secret") or cfg.get("sign_secret") or cfg.get("webhook_secret")
    if not secret:
        return data
    timestamp, sign = _feishu_sign(secret)
    signed_data = dict(data)
    signed_data.update({"timestamp": timestamp, "sign": sign})
    return signed_data


def _send_dingtalk(cfg, recipient, subject, body, payload):
    """钉钉群机器人 markdown 消息。安全设置选『自定义关键词』时，标题须包含关键词。"""
    webhook = cfg.get("webhook_url") or recipient
    if not webhook or not webhook.startswith("http"):
        return False, "钉钉缺 webhook_url"
    data = {
        "msgtype": "markdown",
        "markdown": {"title": subject, "text": f"### {subject}\n\n{body}"},
    }
    return _post_webhook(webhook, data)


def _send_feishu(cfg, recipient, subject, body, payload):
    """飞书群机器人 text 消息。"""
    webhook = cfg.get("webhook_url") or recipient
    if not webhook or not webhook.startswith("http"):
        return False, "飞书缺 webhook_url"
    data = {"msg_type": "text", "content": {"text": f"【{subject}】\n{body}"}}
    return _post_webhook(webhook, _apply_feishu_auth(data, cfg))


def _send_webhook(cfg, recipient, subject, body, payload):
    """通用 POST。body_template 可配置 JSON 模板，{subject}/{body} 占位符替换。"""
    webhook = cfg.get("webhook_url") or recipient
    if not webhook or not webhook.startswith("http"):
        return False, "通用 Webhook 缺 webhook_url"
    preset = (cfg.get("preset") or "").lower()
    if preset == "feishu" and not (cfg.get("body_template") or "").strip():
        data = {"msg_type": "text", "content": {"text": f"【{subject}】\n{body}"}}
        return _post_webhook(webhook, _apply_feishu_auth(data, cfg))
    template = cfg.get("body_template") or '{"text":"【{subject}】\\n{body}"}'
    body_escaped = body.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    subject_escaped = subject.replace('\\', '\\\\').replace('"', '\\"')
    try:
        data = json.loads(template.replace("{subject}", subject_escaped).replace("{body}", body_escaped))
    except json.JSONDecodeError as e:
        return False, f"body_template JSON 错误：{e}"
    return _post_webhook(webhook, data)


_DISPATCH = {
    "inapp": _send_inapp,
    "email": _send_email,
    "sms": _send_sms,
    "wechat_work": _send_wechat_work,
    "qq_bot": _send_qq_bot,
    "dingtalk": _send_dingtalk,
    "feishu": _send_feishu,
    "webhook": _send_webhook,
}


# ============ 预警扫描 ============

def _record_event(rule_type, payload, channels_sent, status):
    with database.get_conn() as conn:
        conn.execute(
            "INSERT INTO alert_event (rule_type, payload_json, notified_channels, status) VALUES (?, ?, ?, ?)",
            (rule_type, json.dumps(payload, ensure_ascii=False, default=str),
             ",".join(channels_sent), status),
        )


def _get_rule(rule_type):
    with database.get_conn() as conn:
        return conn.execute(
            "SELECT * FROM alert_rule WHERE rule_type = ? AND enabled = 1",
            (rule_type,),
        ).fetchone()


def _trigger_rule(rule, subject, body, payload):
    channels = [c.strip() for c in (rule["channel_codes"] or "").split(",") if c.strip()] or ["inapp"]
    user_ids = [int(x) for x in (rule["recipient_user_ids"] or "").split(",") if x.strip().isdigit()]
    users = []
    if user_ids:
        ph = ",".join("?" * len(user_ids))
        with database.get_conn() as conn:
            users = conn.execute(
                f"SELECT id, email, phone FROM user WHERE id IN ({ph})", user_ids
            ).fetchall()

    sent_ok, sent_fail = [], []
    for ch in channels:
        recipient = _build_recipient(ch, users)
        ok, det = notify(ch, recipient, subject, body, payload)
        (sent_ok if ok else sent_fail).append(f"{ch}:{det}")
    status = "sent" if not sent_fail else ("partial" if sent_ok else "failed")
    _record_event(rule["rule_type"], payload, [s.split(":")[0] for s in sent_ok], status)
    return sent_ok, sent_fail


def _build_recipient(channel_code, users):
    """根据渠道类型，从勾选的用户列表里提取对应的联系方式。
    返回空时 notify() 会 fallback 到渠道 default_recipient。"""
    if channel_code == "email":
        return ",".join(u["email"] for u in users if u["email"]) or None
    if channel_code == "sms":
        return ",".join(u["phone"] for u in users if u["phone"]) or None
    # wechat_work / inapp / dingtalk / feishu / webhook / qq_bot 都发到渠道默认收件人
    return None


def scan_low_stock_and_alert():
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT s.code, s.name, s.safety_stock, COALESCE(SUM(i.on_hand),0) AS on_hand
               FROM sku s LEFT JOIN inventory i ON i.sku_id = s.id
               GROUP BY s.id
               HAVING on_hand < s.safety_stock AND s.safety_stock > 0
               ORDER BY (s.safety_stock - on_hand) DESC"""
        ).fetchall()
    if not rows:
        return []
    rule = _get_rule("low_stock")
    if not rule:
        return []
    body = "\n".join(f"{r['code']} {r['name']} 当前 {r['on_hand']} / 安全 {r['safety_stock']}" for r in rows)
    ok, fail = _trigger_rule(rule, f"【低库存】{len(rows)} 个 SKU 低于安全库存",
                              body, {"items": [dict(r) for r in rows]})
    return [("low_stock", len(rows), ok, fail)]


def scan_damage_pending_and_alert():
    with database.get_conn() as conn:
        rows = conn.execute(
            """SELECT d.id, s.code, b.batch_no, d.quantity, d.created_at
               FROM damage_log d JOIN sku s ON s.id = d.sku_id JOIN batch b ON b.id = d.batch_id
               WHERE d.status = 'pending'"""
        ).fetchall()
    if not rows:
        return []
    rule = _get_rule("damage_pending")
    if not rule:
        return []
    body = "\n".join(f"DMG-{r['id']} {r['code']} {r['batch_no']} 数量 {r['quantity']}" for r in rows)
    ok, fail = _trigger_rule(rule, f"【待审】{len(rows)} 张报损单待审批",
                              body, {"items": [dict(r) for r in rows]})
    return [("damage_pending", len(rows), ok, fail)]


def run_all_scans():
    results = []
    results.extend(scan_low_stock_and_alert())
    results.extend(scan_damage_pending_and_alert())
    return results
