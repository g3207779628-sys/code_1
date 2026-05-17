from flask import Flask

import database

app = Flask(__name__)
app.secret_key = "change-me-in-stage-1"


@app.route("/")
def index():
    return (
        "<h1>Hello, code_1</h1>"
        "<p>预制菜 B2C 仓 — 阶段 0 启动成功。</p>"
        "<p>下一步进入阶段 1：主数据 + 登录。</p>"
    )


if __name__ == "__main__":
    database.init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)
