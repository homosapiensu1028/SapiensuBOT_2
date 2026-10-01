import os
import discord
from discord import app_commands
from dotenv import load_dotenv

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

client.run(os.environ["DISCORD_TOKEN"])
