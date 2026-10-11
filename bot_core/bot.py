import os
import sys

if __name__ == "__main__":
    # `python bot_core/bot.py` でも `python -m bot_core.bot` でも今までどおり起動できるように、
    # 本体（import される側の bot_core.bot）に処理を渡します。
    # こうしないと、commands/ から `from bot_core.bot import bot` したときにこのファイルが
    # 2回読み込まれて、別々のbotが2つできてしまい、コマンドが登録されなくなります。
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bot_core.bot import main

    main()
    raise SystemExit

from threading import Thread

import discord
from discord.ext import commands as ext_commands
from dotenv import load_dotenv
from flask import Flask

load_dotenv()


class MyBot(ext_commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # スラッシュコマンド中心なので、メッセージ本文を読む特権インテントは使いません
        super().__init__(command_prefix=ext_commands.when_mentioned, intents=intents)

    async def setup_hook(self):
        # commands/ 以下のコマンドを読み込む
        # （music.py は import するだけで @bot.tree.command が登録されます）
        import commands.music  # noqa: F401

        # 追加機能（もう1曲ボタン・お気に入り・ガチャ・クイズ・リクエスト・リンクチェックなど）
        await self.load_extension("commands.music_extra")
        # 毎日の自動投稿（今日の一曲）。music_extra の後に読み込む
        await self.load_extension("commands.daily")

        # スラッシュコマンドをDiscord側に同期する（起動時に1回）
        await self.tree.sync()

    async def on_ready(self):
        print(f"ログインしました: {self.user}")


bot = MyBot()
# 以前のコードとの互換用（client / tree という名前でも使えます）
client = bot
tree = bot.tree


@bot.tree.command(name="ping", description="pingに返事します")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong!")


# Render無料Web Service用: ポートを開くための小さなWebサーバー
app = Flask(__name__)


@app.route("/")
def home():
    return "ok"


def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("エラー: 環境変数 DISCORD_TOKEN が設定されていません。")
        return
    Thread(target=run_web, daemon=True).start()
    bot.run(token)
