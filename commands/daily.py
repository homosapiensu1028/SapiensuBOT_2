"""
毎日の自動投稿：☀️今日の一曲

・/music_setting →「🧰 管理ツール」→「📅 毎日の自動投稿」で設定したチャンネルに、毎日決まった時刻（JST）に投稿します。
・「今日の一曲」はその日の日付から決まるので、同じ日に何度やっても同じ曲です。
・botが落ちていて投稿時刻を過ぎてしまっても、復帰したその日のうちに1回だけ投稿します（1分ごとにチェック）。
・複数プロセスで動いても二重投稿しないよう、Upstash設定済みなら早い者勝ちの印を使います。
"""

import random
from datetime import datetime

import discord
from discord.ext import commands as ext_commands, tasks

from bot_core.bot import bot
from bot_core.storage import claim_once
from commands import music as M
from commands.music_extra import EXTRA, save_extra, build_song_payload, record_shown


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


def describe_daily(guild: discord.Guild | None) -> str:
    """管理ツールの画面に出す、今の設定の説明。"""
    cfg = EXTRA["daily"].get(str(guild.id)) if guild else None
    if not cfg:
        return "未設定"
    ch = guild.get_channel(cfg.get("channel_id"))
    return f"{ch.mention if ch else '（チャンネル不明）'} に毎日 {cfg.get('hour', 9)}時（JST）"


class DailyModal(discord.ui.Modal, title="毎日の自動投稿（今日の一曲）"):
    """/music_setting の管理ツールから開く。いま開いているチャンネルに投稿します。"""

    def __init__(self, guild: discord.Guild | None):
        super().__init__()
        cfg = EXTRA["daily"].get(str(guild.id)) if guild else None
        self.hour_in = discord.ui.TextInput(
            label="投稿する時刻（日本時間 0〜23）／空欄で停止",
            required=False, max_length=2, default=str(cfg["hour"]) if cfg else "9",
        )
        self.now_in = discord.ui.TextInput(
            label="お試し投稿（yes と入力で今すぐ1回投稿）", required=False, max_length=3,
        )
        self.add_item(self.hour_in)
        self.add_item(self.now_in)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("この設定はサーバー内でのみ使用できます。", ephemeral=True)
            return
        gid = str(interaction.guild.id)
        text = self.hour_in.value.strip()
        if not text:
            EXTRA["daily"].pop(gid, None)
            save_extra()
            await interaction.response.send_message("毎日の自動投稿を停止しました。", ephemeral=True)
            return
        if not text.isdigit() or not 0 <= int(text) <= 23:
            await interaction.response.send_message("時刻は 0〜23 の数字で入力してください。", ephemeral=True)
            return
        hour = int(text)

        cfg = {"channel_id": interaction.channel_id, "hour": hour, "last_date": None}
        now_dt = datetime.now(M.JST)
        today = now_dt.strftime("%Y-%m-%d")
        # 設定した時刻をすでに過ぎている場合は、今日の分は投稿済みとして、明日から始める
        if now_dt.hour >= hour:
            cfg["last_date"] = today
        EXTRA["daily"][gid] = cfg
        save_extra()

        start = "明日" if cfg["last_date"] == today else "今日"
        await interaction.response.send_message(
            f"✅ <#{interaction.channel_id}> に、毎日 {hour}時（JST）に **今日の一曲** を投稿します（{start}から）。",
            ephemeral=True,
        )
        if self.now_in.value.strip().lower() in ("yes", "y"):
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
