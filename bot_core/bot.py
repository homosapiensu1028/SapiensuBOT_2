import os
from threading import Thread

import discord
from discord import app_commands
from dotenv import load_dotenv
from flask import Flask

load_dotenv()

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@tree.command(name="ping", description="pingに返事します")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong!")


@client.event
async def on_ready():
    await tree.sync()
    print(f"ログインしました: {client.user}")


# Render無料Web Service用: ポートを開くための小さなWebサーバー
app = Flask(__name__)


@app.route("/")
def home():
    return "ok"


def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


Thread(target=run_web, daemon=True).start()
client.run(os.environ["DISCORD_TOKEN"])
