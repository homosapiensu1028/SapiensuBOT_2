import json
import os
import random
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands

from bot_core.bot import bot
from bot_core.storage import load_json as _load_json, save_json as _save_json, upstash_enabled

JST = timezone(timedelta(hours=9))


def now_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")

def actor_name(user: discord.abc.User) -> str:
    return getattr(user, "display_name", None) or str(user)


# =========================================================
# 権限設定
# ・下級管理者：/music_setting を開き、曲の閲覧・追加・編集・削除・ログ閲覧などができます
# ・管理者　　：下級管理者にできることに加えて、新規ジャンルの作成、
#              削除済みの曲を倉庫から復元することができます
# 　（下級管理者⊃管理者。管理者のIDは下級管理者のIDにも含めておいてください）
# 対象アカウントのDiscordユーザーIDをカンマ区切りで書き換えてください。
# （開発者モードON → ユーザーを右クリック → 「ユーザーIDをコピー」）
# =========================================================
SUB_ADMIN_IDS = {1378982398875664536, 1552522485075939392, 1332285722350190635, 1400051234693447700}
ADMIN_IDS = {1378982398875664536, 1400051234693447700}


def is_sub_admin(user_id: int) -> bool:
    return user_id in SUB_ADMIN_IDS or user_id in ADMIN_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# =========================================================
# データ保存先
# =========================================================
def _resolve_path(env_key: str, default_path: str, fallback_name: str) -> str:
    path = os.getenv(env_key, default_path)
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        path = fallback_name
    return path


DATA_PATH = _resolve_path(
    "MUSIC_DATA_PATH", "/opt/render/project/src/music_data.json", "music_data.json"
)
DELETED_DATA_PATH = _resolve_path(
    "MUSIC_DELETED_DATA_PATH",
    "/opt/render/project/src/music_deleted_data.json",
    "music_deleted_data.json",
)
GENRES_DATA_PATH = _resolve_path(
    "MUSIC_GENRES_DATA_PATH",
    "/opt/render/project/src/music_genres_data.json",
    "music_genres_data.json",
)
LOGS_DATA_PATH = _resolve_path(
    "MUSIC_LOGS_DATA_PATH",
    "/opt/render/project/src/music_logs_data.json",
    "music_logs_data.json",
)
STATS_DATA_PATH = _resolve_path(
    "MUSIC_STATS_DATA_PATH",
    "/opt/render/project/src/music_stats_data.json",
    "music_stats_data.json",
)


def load_songs():
    return _load_json(DATA_PATH, [])


def save_songs(songs):
    _save_json(DATA_PATH, songs)


def load_deleted_songs():
    return _load_json(DELETED_DATA_PATH, [])


def save_deleted_songs(songs):
    _save_json(DELETED_DATA_PATH, songs)


def load_genres():
    return _load_json(GENRES_DATA_PATH, [])


def save_genres(genres):
    _save_json(GENRES_DATA_PATH, genres)


def load_logs():
    return _load_json(LOGS_DATA_PATH, [])


def save_logs(logs):
    _save_json(LOGS_DATA_PATH, logs)


def load_stats():
    return _load_json(STATS_DATA_PATH, {"music": {}, "setting": {}})


def save_stats(stats):
    _save_json(STATS_DATA_PATH, stats)


if upstash_enabled():
    print("[music] Upstash Redisを永続ストレージとして使用します（再起動しても内容は保持されます）。")
else:
    print("[music] Upstash未設定のため、ローカルJSONファイルを使用します（Renderの無料プランでは再起動で消えます）。")

SONGS = load_songs()
DELETED_SONGS = load_deleted_songs()
GENRES = load_genres()
LOGS = load_logs()
STATS = load_stats()

if not SONGS and not DELETED_SONGS:
    SONGS = [
        {"title": "曲名を入力してください1", "url": "https://youtu.be/xxxxxxxxxxx", "genre": "未分類"},
        {"title": "曲名を入力してください2", "url": "https://youtu.be/xxxxxxxxxxx", "genre": "未分類"},
        {"title": "曲名を入力してください3", "url": "https://youtu.be/xxxxxxxxxxx", "genre": "未分類"},
    ]


# =========================================================
# 旧データからの移行（不足しているフィールドを補完し、idを採番）
# =========================================================
def _migrate_song(song: dict, used_ids: set) -> dict:
    song.setdefault("genre", "未分類")
    song.setdefault("added_by_id", None)
    song.setdefault("added_by_name", "不明")
    song.setdefault("added_at", "")
    song.setdefault("ever_deleted", False)
    if "id" not in song or song["id"] in used_ids:
        new_id = (max(used_ids) + 1) if used_ids else 1
        song["id"] = new_id
    used_ids.add(song["id"])
    return song


def _migrate_all():
    used_ids = set()
    changed = False
    for s in SONGS:
        before = dict(s)
        _migrate_song(s, used_ids)
        if s != before:
            changed = True
    for s in DELETED_SONGS:
        before = dict(s)
        _migrate_song(s, used_ids)
        s.setdefault("deleted_by_id", None)
        s.setdefault("deleted_by_name", "不明")
        s.setdefault("deleted_at", "")
        s.setdefault("delete_reason", "")
        if s != before:
            changed = True
    if changed:
        save_songs(SONGS)
        save_deleted_songs(DELETED_SONGS)
    return used_ids


_USED_IDS = _migrate_all()
_NEXT_ID = (max(_USED_IDS) + 1) if _USED_IDS else 1


def alloc_id() -> int:
    global _NEXT_ID
    i = _NEXT_ID
    _NEXT_ID += 1
    return i


def find_song(song_id: int):
    for i, s in enumerate(SONGS):
        if s.get("id") == song_id:
            return i, s
    return None, None


def find_deleted(song_id: int):
    for i, s in enumerate(DELETED_SONGS):
        if s.get("id") == song_id:
            return i, s
    return None, None


# =========================================================
# ジャンル管理
# =========================================================
def get_all_genres():
    genres = set(GENRES)
    genres.update(s.get("genre", "未分類") for s in SONGS)
    genres.update(s.get("genre", "未分類") for s in DELETED_SONGS)
    genres.add("未分類")
    return sorted(genres)


def register_genre(genre: str):
    if genre not in GENRES:
        GENRES.append(genre)
        save_genres(GENRES)


for _g in get_all_genres():
    if _g not in GENRES:
        GENRES.append(_g)
if GENRES:
    save_genres(GENRES)


async def genre_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    genres = get_all_genres()
    return [
        app_commands.Choice(name=g, value=g)
        for g in genres
        if current.lower() in g.lower()
    ][:25]


async def song_search_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if not is_sub_admin(interaction.user.id):
        return []

    current_lower = current.lower().strip()
    if current_lower:
        candidates = [s for s in SONGS if current_lower in s.get("title", "").lower()]
    else:
        candidates = list(SONGS)

    # 直近に追加された曲を優先して表示（何も入力していない時に便利なため）
    candidates = list(reversed(candidates))[:25]

    choices = []
    for s in candidates:
        label = f"{s['title']}【{s.get('genre', '未分類')}】"[:100]
        choices.append(app_commands.Choice(name=label, value=str(s["id"])))
    return choices


# =========================================================
# ログ・使用回数の記録
# =========================================================
def add_log(entry: dict):
    entry["timestamp"] = now_str()
    LOGS.append(entry)
    save_logs(LOGS)


def bump_music_usage(user: discord.abc.User, genre: str | None):
    uid = str(user.id)
    rec = STATS.setdefault("music", {}).setdefault(
        uid, {"name": actor_name(user), "total": 0, "genres": {}}
   )
    rec["name"] = actor_name(user)
    rec["total"] += 1
    key = genre if genre else "（指定なし）"
    rec["genres"][key] = rec["genres"].get(key, 0) + 1
    save_stats(STATS)


def bump_setting_usage(user: discord.abc.User, field: str):
    uid = str(user.id)
    rec = STATS.setdefault("setting", {}).setdefault(
        uid,
        {"name": actor_name(user), "open": 0, "add": 0, "edit": 0, "delete": 0, "restore": 0},
   )
    rec["name"] = actor_name(user)
    rec[field] = rec.get(field, 0) + 1
    save_stats(STATS)


# =========================================================
# /music : 曲をランダムに紹介
# =========================================================
@bot.tree.command(
    name="music",
    description="登録されている曲の中からランダムに1曲紹介します"
)
@app_commands.describe(
    genre="ジャンルを指定すると、そのジャンルの曲からランダムに選びます（例: ボカロ）",
    count="まとめて紹介する曲数（1〜5）",
    equal="追加した人ごとに均等になるように選ぶ",
    vc="今ボイスチャンネルにいる人が追加した曲だけから選ぶ",
)
@app_commands.autocomplete(genre=genre_autocomplete)
async def music(
    interaction: discord.Interaction,
    genre: str = None,
    count: app_commands.Range[int, 1, 5] = 1,
    equal: bool = False,
    vc: bool = False,
):
    # 追加機能（もう1曲ボタン・サムネ・お気に入りなど）は commands/music_extra.py にあります。
    # 読み込めなかったときは、下の従来処理にフォールバックします。
    try:
        from commands.music_extra import send_music_from_command
    except Exception as e:
        print(f"[music] music_extra を読み込めませんでした。従来の/musicで動作します: {e}")
    else:
        await send_music_from_command(interaction, genre, count, equal, vc)
        return

    if not SONGS:
        await interaction.response.send_message("曲がまだ登録されていません。", ephemeral=True)
        return

    if genre:
        candidates = [
            s for s in SONGS
            if s.get("genre", "未分類").lower() == genre.strip().lower()
        ]
        if not candidates:
            await interaction.response.send_message(
                f"「{genre}」というジャンルの曲は登録されていません。", ephemeral=True
            )
            return
    else:
        candidates = SONGS

    song = random.choice(candidates)
    bump_music_usage(interaction.user, genre.strip() if genre else None)
    await interaction.response.send_message(
        f"**{song['title']}**\n{song['url']}"
    )


# =========================================================
# 共通のページサイズ
# =========================================================
SONGS_PER_PAGE = 10
DELETED_PER_PAGE = 10
LOGS_PER_PAGE = 8

LOG_TYPE_LABELS = {"add": "追加", "edit": "編集", "delete": "削除", "restore": "復元"}
LOG_TYPE_ALIASES = {
    "追加": "add", "add": "add",
    "編集": "edit", "edit": "edit",
    "削除": "delete", "delete": "delete",
    "復元": "restore", "restore": "restore",
}


# =========================================================
# 曲名の一覧出力（コピー用）
# Discordの1メッセージは2000文字までなので、超える場合は複数に分割します
# =========================================================
def build_title_chunks(titles: list, limit: int = 1900) -> list:
    fence = "```"
    chunks = []
    current = []
    size = 0
    for t in titles:
        line = t.replace(fence, "'''").replace("\n", " ")
        add = len(line) + 1
        if current and size + add > limit:
            chunks.append(fence + "\n" + "\n".join(current) + "\n" + fence)
            current, size = [], 0
        current.append(line)
        size += add
    if current:
        chunks.append(fence + "\n" + "\n".join(current) + "\n" + fence)
    return chunks


# =========================================================
# 検索（曲一覧）
# =========================================================
def filter_songs(filters: dict | None):
    if not filters:
        return list(SONGS)
    result = []
    for s in SONGS:
        if filters.get("title"):
            if filters["title"].lower() not in s.get("title", "").lower():
                continue
        if filters.get("genres"):
            song_genre = s.get("genre", "未分類").lower()
            if song_genre not in {g.lower() for g in filters["genres"]}:
                continue
        if filters.get("deleted") is not None:
            if bool(s.get("ever_deleted", False)) != filters["deleted"]:
                continue
        if filters.get("added_by"):
            if filters["added_by"].lower() not in s.get("added_by_name", "").lower():
                continue
        result.append(s)
    return result


def describe_filters(filters: dict | None) -> str:
    if not filters:
        return ""
    parts = []
    if filters.get("title"):
        parts.append(f"曲名:「{filters['title']}」")
    if filters.get("genres"):
        parts.append("ジャンル:" + "・".join(filters["genres"]))
    if filters.get("deleted") is not None:
        parts.append("削除歴:" + ("あり" if filters["deleted"] else "なし"))
    if filters.get("added_by"):
        parts.append(f"追加者:「{filters['added_by']}」")
    return "🔍 検索条件: " + " / ".join(parts) if parts else ""


class SearchModal(discord.ui.Modal, title="曲を検索"):
    def __init__(self, parent_view: "MusicSettingView"):
        super().__init__()
        self.parent_view = parent_view
        f = parent_view.filters or {}

        self.title_input = discord.ui.TextInput(
            label="曲名（部分一致）", required=False, max_length=100,
            default=f.get("title", ""),
        )
        self.genre_input = discord.ui.TextInput(
            label="ジャンル（複数可・「,」区切り）", required=False, max_length=200,
            default="、".join(f.get("genres", [])) if f.get("genres") else "",
        )
        self.deleted_input = discord.ui.TextInput(
            label="以前削除されたことがあるか（はい/いいえ/空欄）", required=False, max_length=10,
            default=("はい" if f.get("deleted") is True else "いいえ" if f.get("deleted") is False else ""),
        )
        self.added_by_input = discord.ui.TextInput(
            label="追加者名（部分一致）", required=False, max_length=100,
            default=f.get("added_by", ""),
        )
        self.add_item(self.title_input)
        self.add_item(self.genre_input)
        self.add_item(self.deleted_input)
        self.add_item(self.added_by_input)

    async def on_submit(self, interaction: discord.Interaction):
        filters = {}
        if self.title_input.value.strip():
            filters["title"] = self.title_input.value.strip()
        if self.genre_input.value.strip():
            raw = self.genre_input.value.replace("、", ",")
            genres = [g.strip() for g in raw.split(",") if g.strip()]
            if genres:
                filters["genres"] = genres
        deleted_val = self.deleted_input.value.strip()
        if deleted_val in ("はい", "yes", "true"):
            filters["deleted"] = True
        elif deleted_val in ("いいえ", "no", "false"):
            filters["deleted"] = False
        if self.added_by_input.value.strip():
            filters["added_by"] = self.added_by_input.value.strip()

        new_view = MusicSettingView(self.parent_view.admin_id, page=0, filters=filters or None)
        embed = new_view.current_embed()
        await interaction.response.edit_message(embed=embed, view=new_view)


# =========================================================
# 曲の追加
# =========================================================
class AddSongModal(discord.ui.Modal, title="曲を追加"):
    def __init__(self, admin_id: int, list_page: int):
        super().__init__()
        self.admin_id = admin_id
        self.list_page = list_page
        self.title_input = discord.ui.TextInput(label="曲名", max_length=100)
        self.url_input = discord.ui.TextInput(label="URL", max_length=300)
        self.add_item(self.title_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction):
        title = self.title_input.value.strip()
        url = self.url_input.value.strip()

        if not title or not url:
            await interaction.response.send_message("曲名とURLの両方を入力してください。", ephemeral=True)
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            await interaction.response.send_message(
                "URLは http:// か https:// から始めてください。", ephemeral=True
            )
            return

        view = GenreChooseView(title, url, self.admin_id, self.list_page)
        embed = discord.Embed(
            title="ジャンルを選択してください",
            description=f"**{title}**\n{url}\n\n既存のジャンルから選ぶか、新しいジャンルを作成してください。",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


class NewGenreModal(discord.ui.Modal, title="新しいジャンルを作成"):
    def __init__(self, draft_title: str, draft_url: str, admin_id: int, list_page: int):
        super().__init__()
        self.draft_title = draft_title
        self.draft_url = draft_url
        self.admin_id = admin_id
        self.list_page = list_page
        self.genre_input = discord.ui.TextInput(label="新しいジャンル名", max_length=50)
        self.add_item(self.genre_input)

    async def on_submit(self, interaction: discord.Interaction):
        genre = self.genre_input.value.strip()
        if not genre:
            await interaction.response.send_message("ジャンル名を入力してください。", ephemeral=True)
            return
        register_genre(genre)
        await finalize_add(interaction, self.draft_title, self.draft_url, genre, self.admin_id, self.list_page)


async def finalize_add(
    interaction: discord.Interaction, title: str, url: str, genre: str,
    admin_id: int, list_page: int,
):
    song = {
        "id": alloc_id(),
        "title": title,
        "url": url,
        "genre": genre,
        "added_by_id": interaction.user.id,
        "added_by_name": actor_name(interaction.user),
        "added_at": now_str(),
        "ever_deleted": False,
    }
    SONGS.append(song)
    save_songs(SONGS)
    add_log({
        "type": "add",
        "actor_id": interaction.user.id,
        "actor_name": actor_name(interaction.user),
        "title": title,
        "url": url,
        "genre": genre,
    })
    bump_setting_usage(interaction.user, "add")

    view = MusicSettingView(admin_id, page=list_page)
    embed = view.current_embed()
    embed.set_footer(text=f"➕ 追加しました: {title}（ジャンル: {genre}）")
    await interaction.response.edit_message(embed=embed, view=view)


class GenreChooseView(discord.ui.View):
    def __init__(self, draft_title: str, draft_url: str, admin_id: int, list_page: int):
        super().__init__(timeout=180)
        self.draft_title = draft_title
        self.draft_url = draft_url
        self.admin_id = admin_id
        self.list_page = list_page

        genres = get_all_genres()[:25] or ["未分類"]
        select = discord.ui.Select(
            placeholder="既存のジャンルから選択",
            options=[discord.SelectOption(label=g, value=g) for g in genres],
        )

        async def _on_select(interaction: discord.Interaction):
            genre = select.values[0]
            await finalize_add(interaction, self.draft_title, self.draft_url, genre, self.admin_id, self.list_page)

        select.callback = _on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🆕 新しいジャンルを作成", style=discord.ButtonStyle.primary)
    async def new_genre_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            NewGenreModal(self.draft_title, self.draft_url, self.admin_id, self.list_page)
        )


    @discord.ui.button(label="🔙 キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MusicSettingView(self.admin_id, page=self.list_page)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)


# =========================================================
# 曲の編集
# =========================================================
class SongEditModal(discord.ui.Modal, title="曲を編集 (1/2)"):
    def __init__(self, song_id: int, parent_view: "SongDetailView"):
        super().__init__()
        self.song_id = song_id
        self.parent_view = parent_view
        _, song = find_song(song_id)

        self.title_input = discord.ui.TextInput(
            label="曲名", default=song["title"], max_length=100
        )
        self.url_input = discord.ui.TextInput(
            label="URL", default=song["url"], max_length=300
        )
        # ジャンルは次のステップで選択するためここでは除外
        self.add_item(self.title_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_title = self.title_input.value.strip()
        new_url = self.url_input.value.strip()

        if not new_title or not new_url:
            await interaction.response.send_message("曲名とURLは必須です。", ephemeral=True)
            return
        if not (new_url.startswith("http://") or new_url.startswith("https://")):
            await interaction.response.send_message(
                "URLは http:// か https:// から始めてください。", ephemeral=True
            )
            return

        # ジャンル選択ビューへ遷移
        view = EditGenreChooseView(
            self.song_id, new_title, new_url,
            self.parent_view.admin_id, self.parent_view.list_page
        )
        embed = discord.Embed(
            title="ジャンルを選択してください (2/2)",
            description=f"**{new_title}**\n{new_url}\n\n既存のジャンルから選ぶか、新しいジャンルを作成してください。\nジャンルを変更しない場合は「✅ このまま保存」を押してください。",
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


class EditNewGenreModal(discord.ui.Modal, title="新しいジャンルを作成"):
    def __init__(self, song_id: int, new_title: str, new_url: str, admin_id: int, list_page: int):
        super().__init__()
        self.song_id = song_id
        self.new_title = new_title
        self.new_url = new_url
        self.admin_id = admin_id
        self.list_page = list_page
        self.genre_input = discord.ui.TextInput(label="新しいジャンル名", max_length=50)
        self.add_item(self.genre_input)

    async def on_submit(self, interaction: discord.Interaction):
        genre = self.genre_input.value.strip()
        if not genre:
            await interaction.response.send_message("ジャンル名を入力してください。", ephemeral=True)
            return
        register_genre(genre)
        await finalize_edit(
            interaction, self.song_id, self.new_title, self.new_url, genre, self.admin_id, self.list_page
        )


async def finalize_edit(
    interaction: discord.Interaction, song_id: int, new_title: str, new_url: str, new_genre: str,
    admin_id: int, list_page: int
):
    idx, song = find_song(song_id)
    if song is None:
        await interaction.response.send_message(
            "曲情報が更新されています。もう一度 /music_setting を実行してください。",
            ephemeral=True,
        )
        return

    changes = []
    if song["title"] != new_title:
        changes.append(f"曲名: 「{song['title']}」→「{new_title}」")
    if song["url"] != new_url:
        changes.append(f"URL: {song['url']} → {new_url}")
    if song.get("genre", "未分類") != new_genre:
        changes.append(f"ジャンル: 「{song.get('genre', '未分類')}」→「{new_genre}」")

    if changes:
        register_genre(new_genre)
        old_title = song["title"]
        SONGS[idx] = {**song, "title": new_title, "url": new_url, "genre": new_genre}
        save_songs(SONGS)
        add_log({
            "type": "edit",
            "actor_id": interaction.user.id,
            "actor_name": actor_name(interaction.user),
            "title": old_title,
            "url": new_url,
            "genre": new_genre,
            "changes": "、".join(changes),
        })
        bump_setting_usage(interaction.user, "edit")

    detail_view = SongDetailView(song_id, admin_id, list_page)
    embed = detail_view.build_detail_embed()
    embed.set_footer(text="✅ 更新しました" if changes else "変更はありませんでした")
    await interaction.response.edit_message(embed=embed, view=detail_view)


class EditGenreChooseView(discord.ui.View):
    def __init__(self, song_id: int, new_title: str, new_url: str, admin_id: int, list_page: int):
        super().__init__(timeout=180)
        self.song_id = song_id
        self.new_title = new_title
        self.new_url = new_url
        self.admin_id = admin_id
        self.list_page = list_page

        _, song = find_song(song_id)
        self.current_genre = song.get("genre", "未分類") if song else "未分類"

        genres = get_all_genres()[:25] or ["未分類"]
        options = []
        for g in genres:
            # 現在設定されているジャンルをデフォルト選択状態にする
            options.append(discord.SelectOption(label=g, value=g, default=(g == self.current_genre)))

        select = discord.ui.Select(
            placeholder="既存のジャンルから選択",
            options=options,
            row=0
        )

        async def _on_select(interaction: discord.Interaction):
            genre = select.values[0]
            await finalize_edit(
                interaction, self.song_id, self.new_title, self.new_url, genre, self.admin_id, self.list_page
            )

        select.callback = _on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ このまま保存", style=discord.ButtonStyle.success, row=1)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ドロップダウンを操作せずに現在のジャンルを維持して保存
        await finalize_edit(
            interaction, self.song_id, self.new_title, self.new_url, self.current_genre, self.admin_id, self.list_page
        )

    @discord.ui.button(label="🆕 新しいジャンルを作成", style=discord.ButtonStyle.primary, row=1)
    async def new_genre_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            EditNewGenreModal(self.song_id, self.new_title, self.new_url, self.admin_id, self.list_page)
        )

    @discord.ui.button(label="🔙 キャンセル", style=discord.ButtonStyle.secondary, row=1)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        detail_view = SongDetailView(self.song_id, self.admin_id, self.list_page)
        embed = detail_view.build_detail_embed()
        await interaction.response.edit_message(embed=embed, view=detail_view)


# =========================================================
# 曲の削除（理由の入力を必須化）
# =========================================================
class DeleteReasonModal(discord.ui.Modal, title="曲を削除"):
    def __init__(self, song_id: int, admin_id: int, list_page: int):
        super().__init__()
        self.song_id = song_id
        self.admin_id = admin_id
        self.list_page = list_page
        self.reason_input = discord.ui.TextInput(
            label="削除理由（57文字以内・必須）", max_length=57, required=True
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        idx, song = find_song(self.song_id)
        if song is None:
            await interaction.response.send_message(
                "曲情報が更新されています。もう一度 /music_setting を実行してください。",
                ephemeral=True,
            )
            return

        reason = self.reason_input.value.strip()
        removed = SONGS.pop(idx)
        save_songs(SONGS)

        deleted_record = {
            **removed,
            "deleted_by_id": interaction.user.id,
            "deleted_by_name": actor_name(interaction.user),
            "deleted_at": now_str(),
            "delete_reason": reason,
        }
        DELETED_SONGS.append(deleted_record)
        save_deleted_songs(DELETED_SONGS)

        add_log({
            "type": "delete",
            "actor_id": interaction.user.id,
            "actor_name": actor_name(interaction.user),
            "title": removed["title"],
            "url": removed["url"],
            "genre": removed.get("genre", "未分類"),
            "reason": reason,
        })
        bump_setting_usage(interaction.user, "delete")

        total = len(SONGS)
        max_page = max(0, (total - 1) // SONGS_PER_PAGE) if total else 0
        page = min(self.list_page, max_page)

        list_view = MusicSettingView(self.admin_id, page)
        embed = list_view.current_embed()
        embed.set_footer(text=f"🗑️ 削除しました: {removed['title']}（倉庫から復元できます）")
        await interaction.response.edit_message(embed=embed, view=list_view)


class SongDetailView(discord.ui.View):
    def __init__(self, song_id: int, admin_id: int, list_page: int):
        super().__init__(timeout=180)
        self.song_id = song_id
        self.admin_id = admin_id
        self.list_page = list_page

    def build_detail_embed(self) -> discord.Embed:
        _, song = find_song(self.song_id)
        title = f"🎵 {song['title']}"[:256]
        url = song["url"][:1024]
        genre = song.get("genre", "未分類")[:1024]

        embed = discord.Embed(title=title, color=discord.Color.blurple())
        embed.add_field(name="URL", value=url, inline=False)
        embed.add_field(name="ジャンル", value=genre, inline=False)
        embed.add_field(
            name="追加者",
            value=f"{song.get('added_by_name', '不明')}（{song.get('added_at', '不明')}）",
            inline=False,
        )
        if song.get("ever_deleted"):
            embed.add_field(name="削除歴", value="以前削除され、復元された曲です", inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このボタンは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⚙️ 設定（編集）", style=discord.ButtonStyle.primary)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, song = find_song(self.song_id)
        if song is None:
            await interaction.response.send_message(
                "曲情報が更新されています。もう一度 /music_setting を実行してください。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(SongEditModal(self.song_id, self))

    @discord.ui.button(label="🗑️ 削除", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, song = find_song(self.song_id)
        if song is None:
            await interaction.response.send_message(
                "曲情報が更新されています。もう一度 /music_setting を実行してください。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            DeleteReasonModal(self.song_id, self.admin_id, self.list_page)
        )

    @discord.ui.button(label="🔙 一覧に戻る", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        list_view = MusicSettingView(self.admin_id, self.list_page)
        embed = list_view.current_embed()
        await interaction.response.edit_message(embed=embed, view=list_view)


class SongSelect(discord.ui.Select):
    def __init__(self, page_songs: list):
        options = [
            discord.SelectOption(
                label=song["title"][:100] or "(無題)",
                description=f"【{song.get('genre', '未分類')}】{song['url']}"[:100],
                value=str(song["id"]),
            )
            for song in page_songs
        ]
        super().__init__(
            placeholder="⚙️ 設定する曲を選択してください（編集・削除）",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        song_id = int(self.values[0])
        parent_view: "MusicSettingView" = self.view

        _, song = find_song(song_id)
        if song is None:
            await interaction.response.send_message(
                "曲情報が更新されています。もう一度 /music_setting を実行してください。",
                ephemeral=True,
            )
            return

        detail_view = SongDetailView(song_id, parent_view.admin_id, parent_view.page)
        embed = detail_view.build_detail_embed()
        await interaction.response.edit_message(embed=embed, view=detail_view)


# =========================================================
# メイン設定画面
# =========================================================
class MusicSettingView(discord.ui.View):
    def __init__(self, admin_id: int, page: int = 0, filters: dict | None = None):
        super().__init__(timeout=180)
        self.admin_id = admin_id
        self.page = page
        self.filters = filters
        self.refresh_items()

    def songs_list(self):
        return filter_songs(self.filters)

    def total_pages(self) -> int:
        total = len(self.songs_list())
        return max(1, (total + SONGS_PER_PAGE - 1) // SONGS_PER_PAGE)

    def current_slice(self):
        songs = self.songs_list()
        start = self.page * SONGS_PER_PAGE
        end = min(start + SONGS_PER_PAGE, len(songs))
        return start, end, songs[start:end]

    def current_embed(self) -> discord.Embed:
        start, end, page_songs = self.current_slice()
        total = len(self.songs_list())

        embed = discord.Embed(title="🎵 登録曲一覧（設定）", color=discord.Color.blurple())

        filter_desc = describe_filters(self.filters)
        if filter_desc:
            embed.add_field(name="\u200b", value=filter_desc, inline=False)

        if not page_songs:
            embed.description = "該当する曲がありません。" if self.filters else "曲がまだ登録されていません。"
            return embed

        embed.description = (
            f"全{total}曲中 {start + 1}〜{end}曲目"
            f"（{self.page + 1}/{self.total_pages()}ページ）\n"
            "下のメニューから曲を選ぶと編集・削除できます。"
        )

        for i, song in enumerate(page_songs):
            flag = "（復元済）" if song.get("ever_deleted") else ""
            field_name = f"{start + i + 1}. {song['title']}{flag} 【{song.get('genre', '未分類')}】"
            embed.add_field(
                name=field_name[:250],
                value=f"{song['url'][:800]}\n追加者: {song.get('added_by_name', '不明')}",
                inline=False,
            )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    def refresh_items(self):
        self.clear_items()
        start, end, page_songs = self.current_slice()
        total = len(self.songs_list())

        if page_songs:
            self.add_item(SongSelect(page_songs))

        add_button = discord.ui.Button(label="➕ 追加", style=discord.ButtonStyle.success, row=1)
        search_button = discord.ui.Button(label="🔍 検索", style=discord.ButtonStyle.primary, row=1)
        warehouse_button = discord.ui.Button(label="🗑️ 倉庫", style=discord.ButtonStyle.secondary, row=1)
        stats_button = discord.ui.Button(label="📊 使用回数", style=discord.ButtonStyle.secondary, row=1)
        logs_button = discord.ui.Button(label="📜 ログ", style=discord.ButtonStyle.secondary, row=1)

        add_button.callback = self._on_add
        search_button.callback = self._on_search
        warehouse_button.callback = self._on_warehouse
        stats_button.callback = self._on_stats
        logs_button.callback = self._on_logs

        self.add_item(add_button)
        self.add_item(search_button)
        self.add_item(warehouse_button)
        self.add_item(stats_button)
        self.add_item(logs_button)

        prev_button = discord.ui.Button(
            label="◀ 前へ", style=discord.ButtonStyle.secondary, row=2,
            disabled=(self.page == 0),
        )
        next_button = discord.ui.Button(
            label="次へ ▶", style=discord.ButtonStyle.secondary, row=2,
            disabled=(end >= total),
        )
        prev_button.callback = self._go_prev
        next_button.callback = self._go_next
        self.add_item(prev_button)
        self.add_item(next_button)

        copy_button = discord.ui.Button(
            label="📋 曲名を一覧出力", style=discord.ButtonStyle.secondary, row=2,
            disabled=(total == 0),
        )
        copy_button.callback = self._on_export_titles
        self.add_item(copy_button)

        tools_button = discord.ui.Button(label="🧰 管理ツール", style=discord.ButtonStyle.primary, row=2)
        tools_button.callback = self._on_tools
        self.add_item(tools_button)

        if self.filters:
            clear_button = discord.ui.Button(label="🔄 検索解除", style=discord.ButtonStyle.secondary, row=2)
            clear_button.callback = self._clear_filters
            self.add_item(clear_button)

    async def _on_export_titles(self, interaction: discord.Interaction):
        # 検索で絞り込み中ならその結果のみ、未検索なら全曲を出力
        titles = [s.get("title", "") for s in self.songs_list()]
        chunks = build_title_chunks(titles)
        if not chunks:
            await interaction.response.send_message("出力する曲がありません。", ephemeral=True)
            return

        scope = "（検索結果のみ）" if self.filters else "（全曲）"
        head = (
            f"📋 曲名一覧 {len(titles)}曲{scope}\n"
            "コードブロック右上の **コピー** ボタン（スマホは長押し→コピー）でクリップボードに入ります。"
        )
        await interaction.response.send_message(head, ephemeral=True)
        for chunk in chunks:
            await interaction.followup.send(chunk, ephemeral=True)

    async def _on_tools(self, interaction: discord.Interaction):
        # プレイリスト・リクエスト承認・書き出し/取り込み・重複/リンクチェック・自動投稿などの管理操作
        try:
            from commands.music_admin import AdminToolsView, tools_embed
        except Exception as e:
            print(f"[music] 管理ツールを読み込めませんでした: {e}")
            await interaction.response.send_message("管理ツールを読み込めませんでした。", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=tools_embed(interaction.guild), view=AdminToolsView(self.admin_id)
        )

    async def _on_add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddSongModal(self.admin_id, self.page))

    async def _on_search(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SearchModal(self))

    async def _on_warehouse(self, interaction: discord.Interaction):
        bump_setting_usage(interaction.user, "open")
        view = MusicWarehouseView(self.admin_id)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_stats(self, interaction: discord.Interaction):
        embed = build_stats_embed()
        view = StatsView(self.admin_id, self.page, self.filters)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _on_logs(self, interaction: discord.Interaction):
        view = LogView(self.admin_id, page=0)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    async def _clear_filters(self, interaction: discord.Interaction):
        view = MusicSettingView(self.admin_id, page=0, filters=None)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    async def _go_prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.refresh_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_next(self, interaction: discord.Interaction):
        self.page = min(self.total_pages() - 1, self.page + 1)
        self.refresh_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


@bot.tree.command(
    name="music_setting",
    description="曲の一覧表示・追加・編集・削除・ログ確認・プレイリストやリクエストの管理などを行います（下級管理者専用）"
)
@app_commands.describe(song="編集・削除したい曲名を入力すると候補が出ます（選ぶと直接その曲の画面が開きます）")
@app_commands.autocomplete(song=song_search_autocomplete)
async def music_setting(interaction: discord.Interaction, song: str = None):
    if not is_sub_admin(interaction.user.id):
        await interaction.response.send_message("このコマンドを使う権限がありません。", ephemeral=True)
        return

    bump_setting_usage(interaction.user, "open")

    if song:
        song_id = None
        try:
            song_id = int(song)
        except ValueError:
            song_id = None

        matched = None
        if song_id is not None:
            _, matched = find_song(song_id)
        if matched is None:
            # 候補以外の文字列がそのまま送信された場合は、曲名の部分一致で検索結果に絞り込む
            view = MusicSettingView(interaction.user.id, page=0, filters={"title": song})
            embed = view.current_embed()
            embed.set_footer(text=f"「{song}」に部分一致する曲を表示しています（候補一覧から選ぶとより確実です）")
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        detail_view = SongDetailView(matched["id"], interaction.user.id, 0)
        embed = detail_view.build_detail_embed()
        await interaction.response.send_message(embed=embed, view=detail_view, ephemeral=True)
        return

    view = MusicSettingView(interaction.user.id)
    embed = view.current_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# =========================================================
# 削除された曲の倉庫（/music_setting の「🗑️ 倉庫」ボタンから）
# =========================================================
class RestoreReasonModal(discord.ui.Modal, title="削除を取り消す"):
    def __init__(self, song_id: int, admin_id: int, list_page: int):
        super().__init__()
        self.song_id = song_id
        self.admin_id = admin_id
        self.list_page = list_page
        self.reason_input = discord.ui.TextInput(
            label="復元理由（100文字以内・必須）", max_length=100, required=True
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        idx, song = find_deleted(self.song_id)
        if song is None:
            await interaction.response.send_message(
                "この曲情報はすでに更新されています。もう一度確認してください。",
                ephemeral=True,
            )
            return

        reason = self.reason_input.value.strip()
        restored = DELETED_SONGS.pop(idx)
        save_deleted_songs(DELETED_SONGS)

        restored_song = {
            k: v for k, v in restored.items()
            if k not in ("deleted_by_id", "deleted_by_name", "deleted_at", "delete_reason")
        }
        restored_song["ever_deleted"] = True
        SONGS.append(restored_song)
        save_songs(SONGS)

        add_log({
            "type": "restore",
            "actor_id": interaction.user.id,
            "actor_name": actor_name(interaction.user),
            "title": restored["title"],
            "url": restored["url"],
            "genre": restored.get("genre", "未分類"),
            "reason": reason,
        })
        bump_setting_usage(interaction.user, "restore")

        total = len(DELETED_SONGS)
        max_page = max(0, (total - 1) // DELETED_PER_PAGE) if total else 0
        page = min(self.list_page, max_page)

        list_view = MusicWarehouseView(self.admin_id, page)
        embed = list_view.current_embed()
        embed.set_footer(text=f"↩️ 復元しました: {restored['title']}（/music で召喚できます）")
        await interaction.response.edit_message(embed=embed, view=list_view)


class DeletedSongDetailView(discord.ui.View):
    def __init__(self, song_id: int, admin_id: int, list_page: int):
        super().__init__(timeout=180)
        self.song_id = song_id
        self.admin_id = admin_id
        self.list_page = list_page

    def build_detail_embed(self) -> discord.Embed:
        _, song = find_deleted(self.song_id)
        title = f"🗑️ {song['title']}"[:256]
        embed = discord.Embed(title=title, color=discord.Color.dark_grey())
        embed.add_field(name="URL", value=song["url"][:1024], inline=False)
        embed.add_field(name="ジャンル", value=song.get("genre", "未分類")[:1024], inline=False)
        embed.add_field(
            name="追加者", value=f"{song.get('added_by_name', '不明')}（{song.get('added_at', '不明')}）",
            inline=False,
        )
        embed.add_field(
            name="削除情報",
            value=(
                f"実行者: {song.get('deleted_by_name', '不明')}\n"
                f"日時: {song.get('deleted_at', '不明')}\n"
                f"理由: {song.get('delete_reason', '（未記入）')}"
            ),
            inline=False,
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このボタンは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="↩️ 元に戻す（管理者のみ）", style=discord.ButtonStyle.success)
    async def restore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message(
                "削除の取り消しは管理者のみ実行できます。", ephemeral=True
            )
            return
        _, song = find_deleted(self.song_id)
        if song is None:
            await interaction.response.send_message(
                "この曲情報はすでに更新されています。もう一度確認してください。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            RestoreReasonModal(self.song_id, self.admin_id, self.list_page)
        )

    @discord.ui.button(label="🔙 一覧に戻る", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        list_view = MusicWarehouseView(self.admin_id, self.list_page)
        embed = list_view.current_embed()
        await interaction.response.edit_message(embed=embed, view=list_view)


class DeletedSongSelect(discord.ui.Select):
    def __init__(self, page_songs: list):
        options = [
            discord.SelectOption(
                label=song["title"][:100] or "(無題)",
                description=f"【{song.get('genre', '未分類')}】{song['url']}"[:100],
                value=str(song["id"]),
            )
            for song in page_songs
        ]
        super().__init__(
            placeholder="曲を選択して詳細を確認・復元",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        song_id = int(self.values[0])
        parent_view: "MusicWarehouseView" = self.view

        _, song = find_deleted(song_id)
        if song is None:
            await interaction.response.send_message(
                "この曲情報はすでに更新されています。もう一度確認してください。",
                ephemeral=True,
            )
            return

        detail_view = DeletedSongDetailView(song_id, parent_view.admin_id, parent_view.page)
        embed = detail_view.build_detail_embed()
        await interaction.response.edit_message(embed=embed, view=detail_view)


class MusicWarehouseView(discord.ui.View):
    def __init__(self, admin_id: int, page: int = 0):
        super().__init__(timeout=180)
        self.admin_id = admin_id
        self.page = page
        self.refresh_items()

    def total_pages(self) -> int:
        return max(1, (len(DELETED_SONGS) + DELETED_PER_PAGE - 1) // DELETED_PER_PAGE)

    def current_slice(self):
        start = self.page * DELETED_PER_PAGE
        end = min(start + DELETED_PER_PAGE, len(DELETED_SONGS))
        return start, end, DELETED_SONGS[start:end]

    def current_embed(self) -> discord.Embed:
        start, end, page_songs = self.current_slice()
        total = len(DELETED_SONGS)

        embed = discord.Embed(title="🗑️ 削除済み曲の倉庫", color=discord.Color.dark_grey())

        if not page_songs:
            embed.description = "削除された曲はありません。"
            return embed

        embed.description = (
            f"全{total}曲中 {start + 1}〜{end}曲目"
            f"（{self.page + 1}/{self.total_pages()}ページ）\n"
            "下のメニューから曲を選ぶと詳細確認・復元（管理者のみ）ができます。"
            )

        for i, song in enumerate(page_songs):
            field_name = f"{start + i + 1}. {song['title']} 【{song.get('genre', '未分類')}】"
            embed.add_field(
                name=field_name[:250],
                value=f"削除者: {song.get('deleted_by_name', '不明')} / 理由: {song.get('delete_reason', '（未記入）')[:200]}",
                inline=False,
            )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    def refresh_items(self):
        self.clear_items()
        start, end, page_songs = self.current_slice()

        if page_songs:
            self.add_item(DeletedSongSelect(page_songs))

        prev_button = discord.ui.Button(label="◀ 前へ", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
        next_button = discord.ui.Button(label="次へ ▶", style=discord.ButtonStyle.secondary, disabled=(end >= len(DELETED_SONGS)))
        back_button = discord.ui.Button(label="🔙 設定に戻る", style=discord.ButtonStyle.secondary)
        prev_button.callback = self._go_prev
        next_button.callback = self._go_next
        back_button.callback = self._go_back
        self.add_item(prev_button)
        self.add_item(next_button)
        self.add_item(back_button)

    async def _go_prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.refresh_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_next(self, interaction: discord.Interaction):
        self.page = min(self.total_pages() - 1, self.page + 1)
        self.refresh_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_back(self, interaction: discord.Interaction):
        view = MusicSettingView(self.admin_id)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)


# =========================================================
# 使用回数
# =========================================================
def build_stats_embed() -> discord.Embed:
    embed = discord.Embed(title="📊 使用回数", color=discord.Color.gold())

    music_stats = STATS.get("music", {})
    if music_stats:
        rows = sorted(music_stats.values(), key=lambda r: r.get("total", 0), reverse=True)[:20]
        lines = []
        for r in rows:
            genre_items = sorted(r.get("genres", {}).items(), key=lambda kv: kv[1], reverse=True)
            top = genre_items[:5]
            rest = len(genre_items) - len(top)
            genre_text = "、".join(f"{g}:{c}回" for g, c in top)
            if rest > 0:
                genre_text += f" ほか{rest}件"
            lines.append(f"**{r['name']}**: 合計 {r.get('total', 0)}回（{genre_text}）")
        text = "\n".join(lines)[:4000] or "記録がありません。"
    else:
        text = "記録がありません。"
    embed.add_field(name="🎵 /music の使用回数", value=text, inline=False)

    setting_stats = STATS.get("setting", {})
    if setting_stats:
        rows = sorted(
            setting_stats.values(),
            key=lambda r: r.get("add", 0) + r.get("edit", 0) + r.get("delete", 0) + r.get("restore", 0) + r.get("open", 0),
            reverse=True,
        )[:20]
        lines = [
            f"**{r['name']}**: 開く{r.get('open', 0)}回 ／ 追加{r.get('add', 0)}回 ／ "
            f"編集{r.get('edit', 0)}回 ／ 削除{r.get('delete', 0)}回 ／ 復元{r.get('restore', 0)}回"
            for r in rows
        ]
        text2 = "\n".join(lines)[:4000] or "記録がありません。"
    else:
        text2 = "記録がありません。"
    embed.add_field(name="⚙️ /music_setting の使用回数", value=text2, inline=False)
    return embed


class StatsView(discord.ui.View):
    def __init__(self, admin_id: int, return_page: int, return_filters: dict | None):
        super().__init__(timeout=180)
        self.admin_id = admin_id
        self.return_page = return_page
        self.return_filters = return_filters

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🔙 設定に戻る", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MusicSettingView(self.admin_id, self.return_page, self.return_filters)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)


# =========================================================
# ログ
# =========================================================
def filter_logs(filters: dict | None):
    if not filters:
        return list(reversed(LOGS))
    result = []
    for entry in reversed(LOGS):
        if filters.get("title") and filters["title"].lower() not in entry.get("title", "").lower():
            continue
        if filters.get("url") and filters["url"].lower() not in entry.get("url", "").lower():
            continue
        if filters.get("actor") and filters["actor"].lower() not in entry.get("actor_name", "").lower():
            continue
        if filters.get("genre") and filters["genre"].lower() not in entry.get("genre", "").lower():
            continue
        if filters.get("type") and entry.get("type") != filters["type"]:
            continue
        result.append(entry)
    return result


def describe_log_filters(filters: dict | None) -> str:
    if not filters:
        return ""
    parts = []
    if filters.get("title"):
        parts.append(f"曲名:「{filters['title']}」")
    if filters.get("url"):
        parts.append(f"URL:「{filters['url']}」")
    if filters.get("actor"):
        parts.append(f"実行者:「{filters['actor']}」")
    if filters.get("genre"):
        parts.append(f"ジャンル:「{filters['genre']}」")
    if filters.get("type"):
        parts.append("種別:" + LOG_TYPE_LABELS.get(filters["type"], filters["type"]))
    return "🔍 検索条件: " + " / ".join(parts) if parts else ""


class LogSearchModal(discord.ui.Modal, title="ログを検索"):
    def __init__(self, parent_view: "LogView"):
        super().__init__()
        self.parent_view = parent_view
        f = parent_view.filters or {}
        self.title_input = discord.ui.TextInput(label="曲名（部分一致）", required=False, max_length=100, default=f.get("title", ""))
        self.url_input = discord.ui.TextInput(label="リンク（部分一致）", required=False, max_length=200, default=f.get("url", ""))
        self.actor_input = discord.ui.TextInput(label="実行者名（部分一致）", required=False, max_length=100, default=f.get("actor", ""))
        self.genre_input = discord.ui.TextInput(label="ジャンル（部分一致）", required=False, max_length=50, default=f.get("genre", ""))
        self.type_input = discord.ui.TextInput(
            label="種別（追加/編集/削除/復元・空欄で全て）", required=False, max_length=10,
            default=LOG_TYPE_LABELS.get(f.get("type"), "") if f.get("type") else "",
        )
        self.add_item(self.title_input)
        self.add_item(self.url_input)
        self.add_item(self.actor_input)
        self.add_item(self.genre_input)
        self.add_item(self.type_input)

    async def on_submit(self, interaction: discord.Interaction):
        filters = {}
        if self.title_input.value.strip():
            filters["title"] = self.title_input.value.strip()
        if self.url_input.value.strip():
            filters["url"] = self.url_input.value.strip()
        if self.actor_input.value.strip():
            filters["actor"] = self.actor_input.value.strip()
        if self.genre_input.value.strip():
            filters["genre"] = self.genre_input.value.strip()
        type_val = self.type_input.value.strip().lower()
        if type_val:
            mapped = LOG_TYPE_ALIASES.get(type_val) or LOG_TYPE_ALIASES.get(self.type_input.value.strip())
            if mapped:
                filters["type"] = mapped

        new_view = LogView(self.parent_view.admin_id, page=0, filters=filters or None)
        embed = new_view.current_embed()
        await interaction.response.edit_message(embed=embed, view=new_view)


class LogView(discord.ui.View):
    def __init__(self, admin_id: int, page: int = 0, filters: dict | None = None):
        super().__init__(timeout=180)
        self.admin_id = admin_id
        self.page = page
        self.filters = filters
        self.refresh_items()

    def logs_list(self):
        return filter_logs(self.filters)

    def total_pages(self) -> int:
        total = len(self.logs_list())
        return max(1, (total + LOGS_PER_PAGE - 1) // LOGS_PER_PAGE)

    def current_slice(self):
        logs = self.logs_list()
        start = self.page * LOGS_PER_PAGE
        end = min(start + LOGS_PER_PAGE, len(logs))
        return start, end, logs[start:end]

    def current_embed(self) -> discord.Embed:
        start, end, page_logs = self.current_slice()
        total = len(self.logs_list())

        embed = discord.Embed(title="📜 操作ログ（曲の追加・編集・削除・復元）", color=discord.Color.dark_teal())

        filter_desc = describe_log_filters(self.filters)
        if filter_desc:
            embed.add_field(name="\u200b", value=filter_desc, inline=False)

        if not page_logs:
            embed.description = "該当するログがありません。" if self.filters else "ログはまだありません。"
            return embed

        embed.description = (
            f"全{total}件中 {start + 1}〜{end}件目"
            f"（{self.page + 1}/{self.total_pages()}ページ／新しい順）"
        )

        for i, entry in enumerate(page_logs):
            label = LOG_TYPE_LABELS.get(entry.get("type"), entry.get("type", "不明"))
            name = f"{start + i + 1}. [{label}] {entry.get('title', '(不明)')}"[:250]

            value_lines = [
                f"日時: {entry.get('timestamp', '不明')}",
                f"実行者: {entry.get('actor_name', '不明')}",
                f"URL: {entry.get('url', '')[:200]}",
                f"ジャンル: {entry.get('genre', '未分類')}",
            ]
            if entry.get("type") == "edit":
                value_lines.append(f"変更内容: {entry.get('changes', '（記録なし）')}")
            if entry.get("type") in ("delete", "restore"):
                value_lines.append(f"理由: {entry.get('reason', '（未記入）')}")

            embed.add_field(name=name, value="\n".join(value_lines)[:1000], inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    def refresh_items(self):
        self.clear_items()
        start, end, page_logs = self.current_slice()
        total = len(self.logs_list())

        search_button = discord.ui.Button(label="🔍 検索", style=discord.ButtonStyle.primary)
        prev_button = discord.ui.Button(label="◀ 前へ", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
        next_button = discord.ui.Button(label="次へ ▶", style=discord.ButtonStyle.secondary, disabled=(end >= total))
        back_button = discord.ui.Button(label="🔙 設定に戻る", style=discord.ButtonStyle.secondary)

        search_button.callback = self._on_search
        prev_button.callback = self._go_prev
        next_button.callback = self._go_next
        back_button.callback = self._go_back

        self.add_item(search_button)
        self.add_item(prev_button)
        self.add_item(next_button)
        self.add_item(back_button)

        if self.filters:
            clear_button = discord.ui.Button(label="🔄 検索解除", style=discord.ButtonStyle.secondary)
            clear_button.callback = self._clear_filters
            self.add_item(clear_button)

    async def _on_search(self, interaction: discord.Interaction):
        await interaction.response.send_modal(LogSearchModal(self))

    async def _clear_filters(self, interaction: discord.Interaction):
        view = LogView(self.admin_id, page=0, filters=None)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    async def _go_prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self.refresh_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_next(self, interaction: discord.Interaction):
        self.page = min(self.total_pages() - 1, self.page + 1)
        self.refresh_items()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    async def _go_back(self, interaction: discord.Interaction):
        view = MusicSettingView(self.admin_id)
        embed = view.current_embed()
        await interaction.response.edit_message(embed=embed, view=view)
