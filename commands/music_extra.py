"""
/music まわりの追加機能（commands/music.py のデータをそのまま使います）

・/music の拡張（もう1曲ボタン・同ジャンルボタン・⭐お気に入り・👍・count/equal/vc オプション）
・/nowplaying      このチャンネルで直前に紹介した曲
・/music_search    曲の検索（誰でも使えます）
・/bgm /bgm_edit /bgm_delete   プリセットプレイリスト
・/music_export /music_import  曲ライブラリのJSON書き出し・取り込み
・/music_dupes     重複曲の検出と倉庫への移動
・/music_gacha     曲ガチャ（紹介回数が少ない曲ほどレア）
・/favorite        お気に入り一覧・ランダム
・/music_ranking   👍ランキング・よく紹介される曲
・/music_request /music_requests   曲のリクエストと承認
・/music_linkcheck YouTubeリンク切れチェック（1日1回の自動チェックも）
・/music_profile   自分（や他の人）の曲ライブラリ上のプロフィール
・/quiz_adder /quiz_ranking   この曲を登録したのは誰？クイズ

保存先は music_extra_data.json（Upstash設定済みなら再起動しても消えません）。
"""

import io
import json
import random
import re
from datetime import datetime, timedelta

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands as ext_commands, tasks

from bot_core.bot import bot
from bot_core.storage import load_json as _load_json, save_json as _save_json
from commands import music as M

# ---------------------------------------------------------
# 保存データ
# ---------------------------------------------------------
EXTRA_PATH = M._resolve_path(
    "MUSIC_EXTRA_DATA_PATH", "/opt/render/project/src/music_extra_data.json", "music_extra_data.json"
)
_DEFAULTS = {
    "favorites": {},   # {user_id: [song_id]}
    "likes": {},       # {song_id: [user_id]}
    "plays": {},       # {song_id: 紹介された回数}
    "last": {},        # {channel_id: {"ids": [...], "at": str}}
    "requests": [],    # リクエスト一覧
    "playlists": {},   # {名前: {"ids": [...], "by": str, "at": str}}
    "quiz": {},        # {種類: {user_id: {"name", "correct", "answered"}}}
    "link_check": {},  # {"at": str, "bad": [...], "notified": [...]}
    "daily": {},       # {guild_id: {...}}  daily.py が使います
}
EXTRA = _load_json(EXTRA_PATH, {})
for _k, _v in _DEFAULTS.items():
    EXTRA.setdefault(_k, type(_v)())


def save_extra():
    _save_json(EXTRA_PATH, EXTRA)


# ---------------------------------------------------------
# URL・サムネイル
# ---------------------------------------------------------
_YT_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|embed/|shorts/|live/)"
    r"|music\.youtube\.com/watch\?(?:[^#\s]*&)?v=)([\w-]{11})"
)


def youtube_id(url: str | None) -> str | None:
    m = _YT_RE.search(url or "")
    return m.group(1) if m else None


def thumb_url(url: str | None) -> str | None:
    vid = youtube_id(url)
    return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else None


def norm_url(url: str | None) -> str:
    vid = youtube_id(url)
    if vid:
        return f"yt:{vid}"
    return (url or "").strip().rstrip("/").lower()


def is_http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def song_by_id(song_id) -> dict | None:
    _, s = M.find_song(int(song_id))
    return s


# ---------------------------------------------------------
# 抽選
# ---------------------------------------------------------
def candidates(genre: str | None = None, user_ids=None) -> list:
    songs = list(M.SONGS)
    if genre:
        g = genre.strip().lower()
        songs = [s for s in songs if s.get("genre", "未分類").lower() == g]
    if user_ids is not None:
        songs = [s for s in songs if s.get("added_by_id") in user_ids]
    return songs


def pick(songs: list, count: int = 1, equal: bool = False, exclude_ids=()) -> list:
    pool = [s for s in songs if s["id"] not in exclude_ids] or list(songs)
    chosen = []
    while pool and len(chosen) < count:
        if equal:
            groups: dict = {}
            for s in pool:
                groups.setdefault(s.get("added_by_id") or s.get("added_by_name"), []).append(s)
            s = random.choice(random.choice(list(groups.values())))
        else:
            s = random.choice(pool)
        chosen.append(s)
        pool.remove(s)
    return chosen


def record_shown(channel_id, songs: list):
    for s in songs:
        k = str(s["id"])
        EXTRA["plays"][k] = EXTRA["plays"].get(k, 0) + 1
    if channel_id:
        EXTRA["last"][str(channel_id)] = {"ids": [s["id"] for s in songs], "at": M.now_str()}
    save_extra()


def fav_count(song_id) -> int:
    sid = int(song_id)
    return sum(1 for ids in EXTRA["favorites"].values() if sid in ids)


def like_count(song_id) -> int:
    return len(EXTRA["likes"].get(str(song_id), []))


def toggle_fav(user_id: int, song_id: int) -> bool:
    ids = EXTRA["favorites"].setdefault(str(user_id), [])
    if song_id in ids:
        ids.remove(song_id)
        now = False
    else:
        ids.append(song_id)
        now = True
    save_extra()
    return now


def toggle_like(user_id: int, song_id: int) -> bool:
    users = EXTRA["likes"].setdefault(str(song_id), [])
    if user_id in users:
        users.remove(user_id)
        now = False
    else:
        users.append(user_id)
        now = True
    save_extra()
    return now


# ---------------------------------------------------------
# 表示（埋め込み・ボタン）
# ---------------------------------------------------------
def song_embed(song: dict, *, image: bool = True, color=None, header: str | None = None) -> discord.Embed:
    url = song.get("url", "")
    e = discord.Embed(
        title=(header + " " if header else "") + song.get("title", "（無題）")[:240],
        url=url if is_http(url) else None,
        color=color or discord.Color.blurple(),
    )
    e.add_field(name="ジャンル", value=song.get("genre", "未分類")[:100], inline=True)
    e.add_field(name="追加者", value=song.get("added_by_name", "不明")[:100], inline=True)
    if image:
        th = thumb_url(url)
        if th:
            e.set_image(url=th)
    sid = str(song["id"])
    e.set_footer(
        text=f"👍 {like_count(sid)}　⭐ {fav_count(sid)}　紹介 {EXTRA['plays'].get(sid, 0)}回"
    )
    return e


class SongView(discord.ui.View):
    """曲を紹介したメッセージに付くボタン。rerun に条件を入れておくと「もう1曲」で同じ条件を使います。"""

    def __init__(self, songs: list, rerun: dict | None = None):
        super().__init__(timeout=900)
        self.songs = songs
        self.rerun = rerun or {}
        if len(songs) != 1:
            self.remove_item(self.fav_button)
            self.remove_item(self.like_button)
            self.remove_item(self.genre_button)

    @discord.ui.button(label="🔁 もう1曲", style=discord.ButtonStyle.primary)
    async def again_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        r = self.rerun
        await send_music(
            interaction, r.get("genre"), r.get("count", 1), r.get("equal", False), r.get("vc_ids"),
            exclude_ids={s["id"] for s in self.songs},
        )

    @discord.ui.button(label="🎯 同ジャンルでもう1曲", style=discord.ButtonStyle.secondary)
    async def genre_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        genre = self.songs[0].get("genre", "未分類")
        await send_music(interaction, genre, 1, False, None, exclude_ids={self.songs[0]["id"]})

    @discord.ui.button(label="⭐ お気に入り", style=discord.ButtonStyle.secondary)
    async def fav_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        now = toggle_fav(interaction.user.id, self.songs[0]["id"])
        await interaction.response.send_message(
            "⭐ お気に入りに追加しました（/favorite で見られます）" if now else "お気に入りから外しました",
            ephemeral=True,
        )

    @discord.ui.button(label="👍", style=discord.ButtonStyle.secondary)
    async def like_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        now = toggle_like(interaction.user.id, self.songs[0]["id"])
        await interaction.response.send_message(
            "👍 いいねしました" if now else "いいねを取り消しました", ephemeral=True
        )


def build_song_payload(songs: list, rerun: dict | None = None, header: str | None = None) -> dict:
    """send_message / channel.send にそのまま渡せる引数を作ります。
    本文にURLを入れておくので、Discordの動画プレビュー（再生プレイヤー）は従来どおり表示されます。"""
    lines = []
    if header:
        lines.append(header)
    for i, s in enumerate(songs, 1):
        prefix = f"{i}. " if len(songs) > 1 else ""
        lines.append(f"{prefix}**{s['title']}**\n{s['url']}")
    content = "\n".join(lines)[:2000]
    embed = song_embed(songs[0], image=False) if len(songs) == 1 else None
    kwargs = {"content": content, "view": SongView(songs, rerun)}
    if embed:
        kwargs["embed"] = embed
    return kwargs


async def send_music(interaction, genre, count, equal, vc_ids, exclude_ids=()):
    songs_all = candidates(genre, vc_ids)
    if not songs_all:
        if vc_ids is not None:
            msg = "VCにいる人が追加した曲がありません。"
        elif genre:
            msg = f"「{genre}」というジャンルの曲は登録されていません。"
        else:
            msg = "曲がまだ登録されていません。"
        await interaction.response.send_message(msg, ephemeral=True)
        return
    chosen = pick(songs_all, count, equal, exclude_ids)
    M.bump_music_usage(interaction.user, genre.strip() if genre else None)
    record_shown(interaction.channel_id, chosen)
    rerun = {"genre": genre, "count": count, "equal": equal, "vc_ids": vc_ids}
    await interaction.response.send_message(**build_song_payload(chosen, rerun))


async def send_music_from_command(interaction, genre, count, equal, vc):
    vc_ids = None
    if vc:
        voice = getattr(interaction.user, "voice", None)
        if not voice or not voice.channel:
            await interaction.response.send_message("先にボイスチャンネルに入ってください。", ephemeral=True)
            return
        vc_ids = frozenset(m.id for m in voice.channel.members)
    await send_music(interaction, genre, count, equal, vc_ids)


# ---------------------------------------------------------
# 一覧から1曲選ぶセレクト（検索・お気に入り・プレイリスト共通）
# ---------------------------------------------------------
class SongPickSelect(discord.ui.Select):
    def __init__(self, songs: list, placeholder="曲を選ぶとチャンネルに表示します"):
        options = [
            discord.SelectOption(
                label=s["title"][:100], value=str(s["id"]), description=s.get("genre", "未分類")[:100]
            )
            for s in songs[:25]
        ]
        super().__init__(placeholder=placeholder, options=options)

    async def callback(self, interaction: discord.Interaction):
        s = song_by_id(self.values[0])
        if s is None:
            await interaction.response.send_message("その曲は削除されたようです。", ephemeral=True)
            return
        record_shown(interaction.channel_id, [s])
        await interaction.response.send_message(**build_song_payload([s], {"genre": s.get("genre")}))


class SongListView(discord.ui.View):
    def __init__(self, songs: list, placeholder="曲を選ぶとチャンネルに表示します"):
        super().__init__(timeout=300)
        if songs:
            self.add_item(SongPickSelect(songs, placeholder))


def list_lines(songs: list, limit: int = 25) -> str:
    lines = []
    for i, s in enumerate(songs[:limit], 1):
        lines.append(f"{i}. {s['title'][:70]} 【{s.get('genre', '未分類')}】")
    if len(songs) > limit:
        lines.append(f"…ほか {len(songs) - limit} 曲")
    return "\n".join(lines)


# =========================================================
# /nowplaying
# =========================================================
@bot.tree.command(name="nowplaying", description="このチャンネルで直前に紹介した曲をもう一度表示します")
async def nowplaying(interaction: discord.Interaction):
    rec = EXTRA["last"].get(str(interaction.channel_id))
    songs = [s for s in (song_by_id(i) for i in (rec or {}).get("ids", [])) if s]
    if not songs:
        await interaction.response.send_message("このチャンネルではまだ曲を紹介していません。", ephemeral=True)
        return
    embeds = [song_embed(s, header="🎧") for s in songs[:5]]
    embeds[0].set_author(name=f"{rec['at']} に紹介")
    await interaction.response.send_message(embeds=embeds, view=SongListView(songs, "この中から表示し直す"))


# =========================================================
# /music_search
# =========================================================
@bot.tree.command(name="music_search", description="登録曲を検索します（曲名・追加者・ジャンル）")
@app_commands.describe(keyword="曲名または追加者名の一部", genre="ジャンルで絞り込み（任意）")
@app_commands.autocomplete(genre=M.genre_autocomplete)
async def music_search(interaction: discord.Interaction, keyword: str, genre: str = None):
    kw = keyword.strip().lower()
    songs = [
        s for s in candidates(genre)
        if kw in s.get("title", "").lower() or kw in s.get("added_by_name", "").lower()
    ]
    if not songs:
        await interaction.response.send_message("見つかりませんでした。", ephemeral=True)
        return
    e = discord.Embed(
        title=f"🔍 「{keyword}」の検索結果 {len(songs)}件", description=list_lines(songs),
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=e, view=SongListView(songs), ephemeral=True)


# =========================================================
# プレイリスト（/bgm）
# =========================================================
async def playlist_autocomplete(interaction: discord.Interaction, current: str):
    names = [n for n in EXTRA["playlists"] if current.lower() in n.lower()]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


@bot.tree.command(name="bgm", description="プリセットプレイリスト（作業用・勉強用など）を表示します")
@app_commands.describe(name="プレイリスト名（省略すると一覧）", random_one="中からランダムに1曲だけ出す")
@app_commands.autocomplete(name=playlist_autocomplete)
async def bgm(interaction: discord.Interaction, name: str = None, random_one: bool = False):
    pls = EXTRA["playlists"]
    if not name:
        if not pls:
            await interaction.response.send_message(
                "まだプレイリストがありません（/bgm_edit で下級管理者が作れます）。", ephemeral=True
            )
            return
        desc = "\n".join(f"・**{n}**（{len(p['ids'])}曲）" for n, p in pls.items())
        await interaction.response.send_message(
            embed=discord.Embed(title="🎼 プレイリスト一覧", description=desc, color=discord.Color.teal())
        )
        return
    pl = pls.get(name)
    if pl is None:
        await interaction.response.send_message("そのプレイリストはありません。", ephemeral=True)
        return
    songs = [s for s in (song_by_id(i) for i in pl["ids"]) if s]
    if not songs:
        await interaction.response.send_message("このプレイリストの曲はすべて削除されています。", ephemeral=True)
        return
    if random_one:
        s = random.choice(songs)
        record_shown(interaction.channel_id, [s])
        await interaction.response.send_message(**build_song_payload([s], {"genre": None}, header=f"🎼 {name}"))
        return
    lines = [f"{i}. [{s['title'][:60]}]({s['url']})" for i, s in enumerate(songs[:25], 1)]
    e = discord.Embed(
        title=f"🎼 {name}（{len(songs)}曲）", description="\n".join(lines)[:4000], color=discord.Color.teal()
    )
    e.set_footer(text=f"作成: {pl.get('by', '不明')}")
    th = thumb_url(songs[0]["url"])
    if th:
        e.set_thumbnail(url=th)
    await interaction.response.send_message(embed=e, view=SongListView(songs))


class PlaylistModal(discord.ui.Modal, title="プレイリストを作成・編集"):
    def __init__(self):
        super().__init__()
        self.name_in = discord.ui.TextInput(label="プレイリスト名（同名なら上書き）", max_length=40)
        self.kw_in = discord.ui.TextInput(label="曲名の検索ワード（任意）", required=False, max_length=50)
        self.genre_in = discord.ui.TextInput(label="ジャンル（任意）", required=False, max_length=40)
        for i in (self.name_in, self.kw_in, self.genre_in):
            self.add_item(i)

    async def on_submit(self, interaction: discord.Interaction):
        kw = self.kw_in.value.strip().lower()
        genre = self.genre_in.value.strip() or None
        songs = [s for s in candidates(genre) if kw in s.get("title", "").lower()]
        if not songs:
            await interaction.response.send_message("条件に合う曲がありません。", ephemeral=True)
            return
        shown = songs[:25]
        note = f"（{len(songs)}曲中、先頭25曲から選べます）" if len(songs) > 25 else ""
        view = PlaylistBuildView(self.name_in.value.strip(), shown, interaction.user.id)
        await interaction.response.send_message(
            f"「{view.name}」に入れる曲を選んでください{note}", view=view, ephemeral=True
        )


class PlaylistBuildView(discord.ui.View):
    def __init__(self, name: str, songs: list, owner_id: int):
        super().__init__(timeout=300)
        self.name, self.owner_id = name, owner_id
        self.selected: list = []
        sel = discord.ui.Select(
            placeholder="曲を選択（複数可）", min_values=1, max_values=len(songs),
            options=[
                discord.SelectOption(label=s["title"][:100], value=str(s["id"]), description=s.get("genre", "")[:100])
                for s in songs
            ],
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        self.selected = [int(v) for v in interaction.data.get("values", [])]
        await interaction.response.defer()

    @discord.ui.button(label="💾 保存", style=discord.ButtonStyle.success)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected:
            await interaction.response.send_message("先に曲を選んでください。", ephemeral=True)
            return
        EXTRA["playlists"][self.name] = {
            "ids": self.selected, "by": M.actor_name(interaction.user), "at": M.now_str()
        }
        save_extra()
        await interaction.response.edit_message(
            content=f"✅ プレイリスト「{self.name}」を保存しました（{len(self.selected)}曲）", view=None
        )


@bot.tree.command(name="bgm_edit", description="プレイリストを作成・上書きします（下級管理者専用）")
async def bgm_edit(interaction: discord.Interaction):
    if not M.is_sub_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return
    await interaction.response.send_modal(PlaylistModal())


@bot.tree.command(name="bgm_delete", description="プレイリストを削除します（下級管理者専用）")
@app_commands.autocomplete(name=playlist_autocomplete)
async def bgm_delete(interaction: discord.Interaction, name: str):
    if not M.is_sub_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return
    if EXTRA["playlists"].pop(name, None) is None:
        await interaction.response.send_message("そのプレイリストはありません。", ephemeral=True)
        return
    save_extra()
    await interaction.response.send_message(f"プレイリスト「{name}」を削除しました。", ephemeral=True)


# =========================================================
# 書き出し・取り込み
# =========================================================
@bot.tree.command(name="music_export", description="登録曲をJSONファイルで書き出します（下級管理者専用）")
async def music_export(interaction: discord.Interaction):
    if not M.is_sub_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return
    data = {
        "exported_at": M.now_str(),
        "songs": M.SONGS,
        "genres": M.get_all_genres(),
        "playlists": EXTRA["playlists"],
    }
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    fname = f"music_export_{datetime.now(M.JST).strftime('%Y%m%d_%H%M')}.json"
    await interaction.response.send_message(
        f"📦 {len(M.SONGS)}曲を書き出しました。/music_import で取り込めます。",
        file=discord.File(io.BytesIO(raw), filename=fname), ephemeral=True,
    )


@bot.tree.command(name="music_import", description="/music_export のJSONから曲を取り込みます（管理者専用）")
@app_commands.describe(file="/music_export で出力したJSONファイル")
async def music_import(interaction: discord.Interaction, file: discord.Attachment):
    if not M.is_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return
    if file.size > 2 * 1024 * 1024:
        await interaction.response.send_message("ファイルが大きすぎます（2MBまで）。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        data = json.loads((await file.read()).decode("utf-8"))
        items = data["songs"] if isinstance(data, dict) else data
        assert isinstance(items, list)
    except Exception:
        await interaction.followup.send("JSONを読み込めませんでした。/music_export のファイルか確認してください。", ephemeral=True)
        return

    existing = {norm_url(s.get("url")) for s in M.SONGS}
    added = skipped = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        title, url = str(it.get("title", "")).strip(), str(it.get("url", "")).strip()
        if not title or not is_http(url) or norm_url(url) in existing:
            skipped += 1
            continue
        genre = str(it.get("genre") or "未分類")[:40]
        M.register_genre(genre)
        M.SONGS.append({
            "id": M.alloc_id(), "title": title[:200], "url": url, "genre": genre,
            "added_by_id": it.get("added_by_id") if isinstance(it.get("added_by_id"), int) else interaction.user.id,
            "added_by_name": str(it.get("added_by_name") or M.actor_name(interaction.user))[:60],
            "added_at": str(it.get("added_at") or M.now_str()), "ever_deleted": False,
        })
        existing.add(norm_url(url))
        added += 1
        M.add_log({
            "type": "add", "actor_id": interaction.user.id, "actor_name": M.actor_name(interaction.user),
            "title": title, "url": url, "genre": genre,
        })
    if added:
        M.save_songs(M.SONGS)
    await interaction.followup.send(
        f"📥 取り込み完了: 追加 {added}曲 / スキップ {skipped}件（URL重複・不正データ）\n"
        "※ プレイリストは曲IDが変わるため取り込まれません。", ephemeral=True,
    )


# =========================================================
# 重複チェック
# =========================================================
def find_dupes() -> list:
    groups: dict = {}
    for s in M.SONGS:
        groups.setdefault(norm_url(s.get("url")), []).append(s)
    return [g for g in groups.values() if len(g) > 1]


class DupeView(discord.ui.View):
    def __init__(self, owner_id: int, groups: list):
        super().__init__(timeout=180)
        self.owner_id, self.groups, self.confirming = owner_id, groups, False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("実行者のみ操作できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🗑️ 古い方を残して重複を倉庫へ", style=discord.ButtonStyle.danger)
    async def merge_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.confirming:
            self.confirming = True
            button.label = "⚠️ もう一度押すと実行します"
            await interaction.response.edit_message(view=self)
            return
        moved = 0
        for g in self.groups:
            for s in g[1:]:
                idx, cur = M.find_song(s["id"])
                if cur is None:
                    continue
                M.SONGS.pop(idx)
                M.DELETED_SONGS.append({
                    **cur, "deleted_by_id": interaction.user.id,
                    "deleted_by_name": M.actor_name(interaction.user),
                    "deleted_at": M.now_str(), "delete_reason": "重複のため自動整理",
                })
                M.add_log({
                    "type": "delete", "actor_id": interaction.user.id,
                    "actor_name": M.actor_name(interaction.user), "title": cur["title"],
                    "url": cur["url"], "genre": cur.get("genre", "未分類"), "reason": "重複のため自動整理",
                })
                moved += 1
        M.save_songs(M.SONGS)
        M.save_deleted_songs(M.DELETED_SONGS)
        await interaction.response.edit_message(
            content=f"✅ {moved}曲を倉庫へ移動しました（/music_setting の倉庫から復元できます）", embed=None, view=None
        )


@bot.tree.command(name="music_dupes", description="URLが重複している曲を検出します（下級管理者専用）")
async def music_dupes(interaction: discord.Interaction):
    if not M.is_sub_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return
    groups = find_dupes()
    if not groups:
        await interaction.response.send_message("重複している曲はありません ✨", ephemeral=True)
        return
    lines = []
    for g in groups[:15]:
        lines.append(f"**{g[0]['title'][:60]}** ×{len(g)}（" + " / ".join(s["title"][:20] for s in g[1:]) + "）")
    e = discord.Embed(
        title=f"♻️ 重複 {len(groups)}組", description="\n".join(lines)[:4000], color=discord.Color.orange()
    )
    e.set_footer(text="登録が古い方を残し、新しい方を倉庫へ移動します")
    await interaction.response.send_message(embed=e, view=DupeView(interaction.user.id, groups), ephemeral=True)


# =========================================================
# 曲ガチャ
# =========================================================
RARITIES = [
    (0.9, "🌈 SSR", discord.Color.gold()),
    (0.7, "💜 SR", discord.Color.purple()),
    (0.4, "💙 R", discord.Color.blue()),
    (0.0, "⚪ N", discord.Color.light_grey()),
]


def rarity_of(song: dict):
    plays = EXTRA["plays"]
    mine = plays.get(str(song["id"]), 0)
    n = len(M.SONGS)
    higher = sum(1 for s in M.SONGS if plays.get(str(s["id"]), 0) > mine)
    ratio = higher / n if n else 0
    for threshold, label, color in RARITIES:
        if ratio >= threshold:
            if mine == 0 and label == "⚪ N":
                return "💙 R", discord.Color.blue()
            return label, color
    return "⚪ N", discord.Color.light_grey()


@bot.tree.command(name="music_gacha", description="曲ガチャ！今まであまり紹介されていない曲ほどレアです")
async def music_gacha(interaction: discord.Interaction):
    if not M.SONGS:
        await interaction.response.send_message("曲がまだ登録されていません。", ephemeral=True)
        return
    weights = [1 / (EXTRA["plays"].get(str(s["id"]), 0) + 1) for s in M.SONGS]
    song = random.choices(M.SONGS, weights=weights, k=1)[0]
    label, color = rarity_of(song)
    record_shown(interaction.channel_id, [song])
    e = song_embed(song, color=color, header=label)
    await interaction.response.send_message(
        content=f"🎰 ガチャ結果は… **{label}**！",
        embed=e, view=SongView([song], {"genre": None}),
    )


# =========================================================
# お気に入り・ランキング
# =========================================================
@bot.tree.command(name="favorite", description="⭐お気に入りの曲を表示します")
@app_commands.describe(random_one="お気に入りの中からランダムに1曲出す")
async def favorite(interaction: discord.Interaction, random_one: bool = False):
    songs = [s for s in (song_by_id(i) for i in EXTRA["favorites"].get(str(interaction.user.id), [])) if s]
    if not songs:
        await interaction.response.send_message(
            "お気に入りがありません。/music の ⭐ ボタンで追加できます。", ephemeral=True
        )
        return
    if random_one:
        s = random.choice(songs)
        record_shown(interaction.channel_id, [s])
        await interaction.response.send_message(
            **build_song_payload([s], {"genre": None}, header=f"⭐ {M.actor_name(interaction.user)}のお気に入りから")
        )
        return
    e = discord.Embed(title=f"⭐ お気に入り {len(songs)}曲", description=list_lines(songs), color=discord.Color.gold())
    await interaction.response.send_message(embed=e, view=SongListView(songs), ephemeral=True)


@bot.tree.command(name="music_ranking", description="👍が多い曲・よく紹介される曲のランキング")
async def music_ranking(interaction: discord.Interaction):
    def top(key_fn, n=5):
        rows = sorted(M.SONGS, key=key_fn, reverse=True)[:n]
        return [(s, key_fn(s)) for s in rows if key_fn(s) > 0]

    e = discord.Embed(title="🏆 曲ランキング", color=discord.Color.gold())
    for title, fn, unit in (
        ("👍 いいね", lambda s: like_count(s["id"]), "👍"),
        ("⭐ お気に入り", lambda s: fav_count(s["id"]), "⭐"),
        ("🎲 よく紹介された曲", lambda s: EXTRA["plays"].get(str(s["id"]), 0), "回"),
    ):
        rows = top(fn)
        e.add_field(
            name=title,
            value="\n".join(f"{i}. {s['title'][:50]}（{v}{unit}）" for i, (s, v) in enumerate(rows, 1)) or "まだデータがありません",
            inline=False,
        )
    await interaction.response.send_message(embed=e)


# =========================================================
# リクエスト
# =========================================================
@bot.tree.command(name="music_request", description="追加してほしい曲をリクエストします（下級管理者が承認すると登録されます）")
@app_commands.describe(title="曲名", url="曲のURL", genre="ジャンル（任意）")
@app_commands.autocomplete(genre=M.genre_autocomplete)
async def music_request(interaction: discord.Interaction, title: str, url: str, genre: str = None):
    url = url.strip()
    if not is_http(url):
        await interaction.response.send_message("URLは http:// か https:// で始まる形式にしてください。", ephemeral=True)
        return
    key = norm_url(url)
    if any(norm_url(s.get("url")) == key for s in M.SONGS):
        await interaction.response.send_message("その曲はすでに登録されています 🎉", ephemeral=True)
        return
    if any(r["status"] == "pending" and norm_url(r["url"]) == key for r in EXTRA["requests"]):
        await interaction.response.send_message("その曲はすでにリクエスト済みです。", ephemeral=True)
        return
    rid = max([r["id"] for r in EXTRA["requests"]] + [0]) + 1
    EXTRA["requests"].append({
        "id": rid, "title": title.strip()[:200], "url": url, "genre": (genre or "未分類").strip()[:40],
        "by_id": interaction.user.id, "by_name": M.actor_name(interaction.user),
        "at": M.now_str(), "status": "pending",
    })
    # 古い処理済みリクエストは溜めすぎない
    done = [r for r in EXTRA["requests"] if r["status"] != "pending"]
    if len(done) > 200:
        for r in done[:-200]:
            EXTRA["requests"].remove(r)
    save_extra()
    await interaction.response.send_message(
        f"📨 リクエストを受け付けました（#{rid}）。承認されると登録されます。", ephemeral=True
    )


class RequestsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.selected: int | None = None
        self.rebuild()

    def pending(self) -> list:
        return [r for r in EXTRA["requests"] if r["status"] == "pending"]

    def embed(self) -> discord.Embed:
        p = self.pending()
        e = discord.Embed(title=f"📨 曲リクエスト（未処理 {len(p)}件）", color=discord.Color.orange())
        if not p:
            e.description = "未処理のリクエストはありません。"
            return e
        sel = next((r for r in p if r["id"] == self.selected), None)
        if sel:
            e.description = (
                f"**{sel['title']}**\n{sel['url']}\nジャンル: {sel['genre']}\n"
                f"リクエスト者: {sel['by_name']}（{sel['at']}）"
            )
            th = thumb_url(sel["url"])
            if th:
                e.set_image(url=th)
        else:
            e.description = "\n".join(f"#{r['id']} {r['title'][:50]}（{r['by_name']}）" for r in p[:25])
        return e

    def rebuild(self):
        self.clear_items()
        p = self.pending()[:25]
        if p:
            sel = discord.ui.Select(
                placeholder="リクエストを選択",
                options=[
                    discord.SelectOption(label=f"#{r['id']} {r['title']}"[:100], value=str(r["id"]),
                                         description=r["by_name"][:100], default=(r["id"] == self.selected))
                    for r in p
                ],
            )
            sel.callback = self._on_select
            self.add_item(sel)
        ok = discord.ui.Button(label="✅ 承認して登録", style=discord.ButtonStyle.success, disabled=self.selected is None)
        ng = discord.ui.Button(label="❌ 却下", style=discord.ButtonStyle.danger, disabled=self.selected is None)
        ok.callback, ng.callback = self._approve, self._reject
        self.add_item(ok)
        self.add_item(ng)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("実行者のみ操作できます。", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        self.selected = int(interaction.data["values"][0])
        self.rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    def _current(self):
        return next((r for r in self.pending() if r["id"] == self.selected), None)

    async def _approve(self, interaction: discord.Interaction):
        r = self._current()
        if r is None:
            await interaction.response.send_message("すでに処理されています。", ephemeral=True)
            return
        M.register_genre(r["genre"])
        M.SONGS.append({
            "id": M.alloc_id(), "title": r["title"], "url": r["url"], "genre": r["genre"],
            "added_by_id": r["by_id"], "added_by_name": r["by_name"], "added_at": M.now_str(), "ever_deleted": False,
        })
        M.save_songs(M.SONGS)
        M.add_log({
            "type": "add", "actor_id": interaction.user.id, "actor_name": M.actor_name(interaction.user),
            "title": r["title"], "url": r["url"], "genre": r["genre"],
        })
        r["status"] = "approved"
        save_extra()
        self.selected = None
        self.rebuild()
        e = self.embed()
        e.set_footer(text=f"✅ 「{r['title'][:40]}」を登録しました")
        await interaction.response.edit_message(embed=e, view=self)

    async def _reject(self, interaction: discord.Interaction):
        r = self._current()
        if r is None:
            await interaction.response.send_message("すでに処理されています。", ephemeral=True)
            return
        r["status"] = "rejected"
        save_extra()
        self.selected = None
        self.rebuild()
        e = self.embed()
        e.set_footer(text=f"❌ 「{r['title'][:40]}」を却下しました")
        await interaction.response.edit_message(embed=e, view=self)


@bot.tree.command(name="music_requests", description="曲リクエストの承認・却下（下級管理者専用）")
async def music_requests(interaction: discord.Interaction):
    if not M.is_sub_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return
    view = RequestsView(interaction.user.id)
    await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)


# =========================================================
# リンク切れチェック（YouTubeのoEmbedで確認。APIキー不要）
# =========================================================
_check_running = False


async def run_link_check() -> list:
    """YouTube曲だけを確認し、問題がありそうな曲のリストを返す。"""
    global _check_running
    if _check_running:
        return EXTRA["link_check"].get("bad", [])
    _check_running = True
    bad = []
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for s in list(M.SONGS):
                vid = youtube_id(s.get("url"))
                if not vid:
                    continue
                try:
                    async with session.get(
                        "https://www.youtube.com/oembed",
                        params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                    ) as r:
                        status = r.status
                except Exception:
                    status = None
                if status == 404:
                    bad.append({"id": s["id"], "title": s["title"], "reason": "削除された／存在しない"})
                elif status in (401, 403):
                    bad.append({"id": s["id"], "title": s["title"], "reason": "非公開 または 埋め込み不可"})
                await _sleep(0.4)
    finally:
        _check_running = False
    old = EXTRA["link_check"]
    EXTRA["link_check"] = {"at": M.now_str(), "bad": bad, "notified": old.get("notified", []), "ts": datetime.now(M.JST).timestamp()}
    save_extra()
    return bad


async def _sleep(sec: float):
    import asyncio
    await asyncio.sleep(sec)


def link_report_embed(bad: list, checked_at: str) -> discord.Embed:
    e = discord.Embed(
        title=f"🔗 リンクチェック結果（{checked_at}）",
        description="\n".join(f"・{b['title'][:60]} … {b['reason']}" for b in bad[:25]) or "問題のあるリンクは見つかりませんでした ✨",
        color=discord.Color.red() if bad else discord.Color.green(),
    )
    if len(bad) > 25:
        e.set_footer(text=f"ほか {len(bad) - 25}件")
    else:
        e.set_footer(text="YouTube以外のURLは確認していません")
    return e


@bot.tree.command(name="music_linkcheck", description="登録曲のYouTubeリンク切れを確認します（下級管理者専用）")
async def music_linkcheck(interaction: discord.Interaction):
    if not M.is_sub_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    bad = await run_link_check()
    await interaction.followup.send(embed=link_report_embed(bad, EXTRA["link_check"].get("at", "")), ephemeral=True)


class MusicExtraCog(ext_commands.Cog):
    def __init__(self, bot_):
        self.bot = bot_

    async def cog_load(self):
        self.auto_link_check.start()

    async def cog_unload(self):
        self.auto_link_check.cancel()

    @tasks.loop(hours=6)
    async def auto_link_check(self):
        # 再起動のたびに走らないよう、前回から20時間以上あいているときだけ実行
        last = EXTRA["link_check"].get("ts", 0)
        if datetime.now(M.JST).timestamp() - last < 20 * 3600:
            return
        bad = await run_link_check()
        notified = set(EXTRA["link_check"].get("notified", []))
        fresh = [b for b in bad if b["id"] not in notified]
        if not fresh:
            return
        for uid in M.ADMIN_IDS:
            try:
                user = await self.bot.fetch_user(uid)
                await user.send(embed=link_report_embed(fresh, EXTRA["link_check"]["at"]))
            except Exception:
                pass
        EXTRA["link_check"]["notified"] = sorted(notified | {b["id"] for b in fresh})
        save_extra()

    @auto_link_check.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


# =========================================================
# プロフィール
# =========================================================
@bot.tree.command(name="music_profile", description="曲ライブラリでの自分（や他の人）のプロフィールを表示します")
async def music_profile(interaction: discord.Interaction, user: discord.User = None):
    user = user or interaction.user
    uid = str(user.id)
    mine = [s for s in M.SONGS if s.get("added_by_id") == user.id]
    genre_cnt: dict = {}
    for s in mine:
        g = s.get("genre", "未分類")
        genre_cnt[g] = genre_cnt.get(g, 0) + 1
    usage = M.STATS.get("music", {}).get(uid, {})
    use_genres = sorted(usage.get("genres", {}).items(), key=lambda kv: kv[1], reverse=True)[:3]
    likes_got = sum(like_count(s["id"]) for s in mine)
    plays_got = sum(EXTRA["plays"].get(str(s["id"]), 0) for s in mine)
    quiz = EXTRA["quiz"].get("adder", {}).get(uid, {})

    e = discord.Embed(title=f"🎧 {M.actor_name(user)} のプロフィール", color=discord.Color.blurple())
    e.set_thumbnail(url=user.display_avatar.url)
    e.add_field(name="登録した曲", value=f"{len(mine)}曲", inline=True)
    e.add_field(name="曲が紹介された回数", value=f"{plays_got}回", inline=True)
    e.add_field(name="もらった👍", value=f"{likes_got}", inline=True)
    e.add_field(name="/music の使用回数", value=f"{usage.get('total', 0)}回", inline=True)
    e.add_field(name="⭐ お気に入り", value=f"{len(EXTRA['favorites'].get(uid, []))}曲", inline=True)
    e.add_field(name="クイズ正解", value=f"{quiz.get('correct', 0)} / {quiz.get('answered', 0)}", inline=True)
    top_g = sorted(genre_cnt.items(), key=lambda kv: kv[1], reverse=True)[:3]
    e.add_field(name="よく登録するジャンル", value="、".join(f"{g}({c})" for g, c in top_g) or "なし", inline=False)
    e.add_field(name="よく指定するジャンル", value="、".join(f"{g}({c})" for g, c in use_genres if g != "（指定なし）") or "なし", inline=False)
    await interaction.response.send_message(embed=e)


# =========================================================
# クイズ共通（選択式・時間切れで答え合わせ）
# =========================================================
class ChoiceQuizView(discord.ui.View):
    def __init__(self, choices: list, correct_key: str, kind: str, reveal_embed, exclude_user_ids=(), timeout=30):
        """choices: [(ラベル, キー)] / reveal_embed: (正解者名リスト, 回答者数) -> Embed"""
        super().__init__(timeout=timeout)
        self.correct_key, self.kind, self.reveal_embed = correct_key, kind, reveal_embed
        self.exclude = set(exclude_user_ids)
        self.answers: dict = {}   # user_id -> (name, 正解か)
        self.message: discord.Message | None = None
        for label, key in choices:
            btn = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(key)
            self.add_item(btn)

    def _make_cb(self, key: str):
        async def cb(interaction: discord.Interaction):
            uid = interaction.user.id
            if uid in self.exclude:
                await interaction.response.send_message("あなたは出題の関係者なので回答できません 🙅", ephemeral=True)
                return
            if uid in self.answers:
                await interaction.response.send_message("回答済みです。結果発表をお待ちください！", ephemeral=True)
                return
            self.answers[uid] = (M.actor_name(interaction.user), key == self.correct_key)
            await interaction.response.send_message("回答を受け付けました。結果発表をお待ちください ⏳", ephemeral=True)
        return cb

    async def on_timeout(self):
        board = EXTRA["quiz"].setdefault(self.kind, {})
        winners = []
        for uid, (name, ok) in self.answers.items():
            rec = board.setdefault(str(uid), {"name": name, "correct": 0, "answered": 0})
            rec["name"] = name
            rec["answered"] += 1
            if ok:
                rec["correct"] += 1
                winners.append(name)
        if self.answers:
            save_extra()
        if self.message:
            try:
                await self.message.edit(embed=self.reveal_embed(winners, len(self.answers)), view=None)
            except Exception:
                pass


@bot.tree.command(name="quiz_adder", description="この曲を登録したのは誰？クイズ")
async def quiz_adder(interaction: discord.Interaction):
    adders = {s["added_by_id"]: s.get("added_by_name", "不明") for s in M.SONGS if s.get("added_by_id")}
    pool = [s for s in M.SONGS if s.get("added_by_id")]
    if len(adders) < 2 or not pool:
        await interaction.response.send_message("登録者が2人以上いないとクイズを出せません。", ephemeral=True)
        return
    song = random.choice(pool)
    correct = song["added_by_id"]
    others = random.sample([i for i in adders if i != correct], min(3, len(adders) - 1))
    ids = others + [correct]
    random.shuffle(ids)

    q = discord.Embed(
        title="🎵 この曲を登録したのは誰？",
        description=f"**{song['title']}**\nジャンル: {song.get('genre', '未分類')}\n\n⏱ 30秒以内にボタンで回答！",
        color=discord.Color.orange(),
    )
    th = thumb_url(song["url"])
    if th:
        q.set_image(url=th)

    def reveal(winners, n):
        e = discord.Embed(
            title="🎉 結果発表",
            description=(
                f"正解は **{adders[correct]}** さん！\n**{song['title']}**\n{song['url']}\n\n"
                f"回答 {n}人 / 正解者: {'、'.join(winners) if winners else 'なし'}"
            ),
            color=discord.Color.green(),
        )
        return e

    view = ChoiceQuizView([(adders[i], str(i)) for i in ids], str(correct), "adder", reveal, exclude_user_ids={correct})
    await interaction.response.send_message(embed=q, view=view)
    view.message = await interaction.original_response()


@bot.tree.command(name="quiz_ranking", description="曲の登録者当てクイズの正解数ランキング")
async def quiz_ranking(interaction: discord.Interaction):
    board = EXTRA["quiz"].get("adder", {})
    rows = sorted(board.values(), key=lambda r: (r["correct"], -r["answered"]), reverse=True)[:10]
    e = discord.Embed(title="🏆 クイズランキング（曲の登録者当て）", color=discord.Color.gold())
    e.description = "\n".join(
        f"{i}. {r['name']} … {r['correct']}正解 / {r['answered']}回答" for i, r in enumerate(rows, 1)
    ) or "まだ記録がありません。"
    await interaction.response.send_message(embed=e)


async def setup(bot_):
    await bot_.add_cog(MusicExtraCog(bot_))
