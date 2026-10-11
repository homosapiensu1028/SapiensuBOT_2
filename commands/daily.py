"""
毎日の自動投稿：☀️今日の一曲

・/daily_setting を実行したチャンネルに、毎日決まった時刻（JST）に投稿します。
・「今日の一曲」はその日の日付から決まるので、同じ日に何度やっても同じ曲です。
・botが落ちていて投稿時刻を過ぎてしまっても、復帰したその日のうちに1回だけ投稿します（1分ごとにチェック）。
・複数プロセスで動いても二重投稿しないよう、Upstash設定済みなら早い者勝ちの印を使います。
"""

import random
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands as ext_commands, tasks

from bot_core.bot import bot
from bot_core.storage import claim_once
from commands import music as M
from commands.music_extra import EXTRA, save_extra, build_song_payload, record_shown


def _allowed(interaction: discord.Interaction) -> bool:
    if M.is_sub_admin(interaction.user.id):
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and (perms.manage_guild or perms.administrator))


async def post_daily(guild_id: int, cfg: dict, today: str):
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(cfg.get("channel_id")) if guild else None
    if channel is None or not M.SONGS:
        return
    try:
        song = random.Random(f"{today}-{guild_id}").choice(M.SONGS)
        record_shown(channel.id, [song])
        await channel.send(**build_song_payload([song], {"genre": None}, header="☀️ **今日の一曲**"))
    except (discord.Forbidden, discord.HTTPException) as ex:
        print(f"[daily] 投稿に失敗しました（guild={guild_id}）: {ex}")


@bot.tree.command(name="daily_setting", description="毎日の自動投稿（今日の一曲）を、このチャンネルに設定します")
@app_commands.describe(
    enable="ON：このチャンネルに毎日投稿 / OFF：停止",
    hour="投稿する時刻（日本時間・0〜23時）",
    now="設定したあと、お試しで今すぐ1回投稿する",
)
async def daily_setting(
    interaction: discord.Interaction,
    enable: bool = True,
    hour: app_commands.Range[int, 0, 23] = 9,
    now: bool = False,
):
    if interaction.guild is None:
        await interaction.response.send_message("このコマンドはサーバー内でのみ使用できます。", ephemeral=True)
        return
    if not _allowed(interaction):
        await interaction.response.send_message("下級管理者か、サーバー管理権限のある人だけが設定できます。", ephemeral=True)
        return

    gid = str(interaction.guild.id)
    if not enable:
        EXTRA["daily"].pop(gid, None)
        save_extra()
        await interaction.response.send_message("毎日の自動投稿を停止しました。", ephemeral=True)
        return

    cfg = {"channel_id": interaction.channel_id, "hour": hour, "last_date": None}
    today = datetime.now(M.JST).strftime("%Y-%m-%d")
    # 設定した時刻をすでに過ぎている場合は、今日の分は投稿済みとして、明日から始める
    if datetime.now(M.JST).hour >= hour:
        cfg["last_date"] = today
    EXTRA["daily"][gid] = cfg
    save_extra()

    start = "明日" if cfg["last_date"] == today else "今日"
    await interaction.response.send_message(
        f"✅ {interaction.channel.mention} に、毎日 {hour}時（JST）に **今日の一曲** を投稿します（{start}から）。",
        ephemeral=True,
    )
    if now:
        await post_daily(interaction.guild.id, cfg, today)


class DailyCog(ext_commands.Cog):
    def __init__(self, bot_):
        self.bot = bot_

    async def cog_load(self):
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self):
        now = datetime.now(M.JST)
        today = now.strftime("%Y-%m-%d")
        for gid, cfg in list(EXTRA["daily"].items()):
            if cfg.get("last_date") == today or now.hour < cfg.get("hour", 9):
                continue
            # 先に「投稿済み」にしておく（投稿でエラーが出ても1分ごとに連投しないため）
            cfg["last_date"] = today
            save_extra()
            if not claim_once(f"daily:{gid}:{today}", 172800):
                continue
            await post_daily(int(gid), cfg, today)

    @tick.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot_):
    await bot_.add_cog(DailyCog(bot_))
