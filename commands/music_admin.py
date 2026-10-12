"""
/music_setting の「🧰 管理ツール」画面

曲ライブラリの管理者向けの操作を、スラッシュコマンドを増やさずに /music_setting にまとめています。
（実際の処理は commands/music_extra.py と commands/daily.py にあります）

  🎼 プレイリスト作成 / 🗑️ プレイリスト削除
  📨 リクエスト承認（未処理の件数つき）
  ♻️ 重複チェック / 🔗 リンクチェック
  📦 書き出し / 📥 取り込み（管理者のみ）
  📅 毎日の自動投稿（今日の一曲）
"""

import re

import discord

from commands import music as M
from commands import music_extra as X
from commands.daily import DailyModal, describe_daily

MSG_LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(?:\d+|@me)/(\d+)/(\d+)")
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def tools_embed(guild: discord.Guild | None) -> discord.Embed:
    pending = sum(1 for r in X.EXTRA["requests"] if r["status"] == "pending")
    lc = X.EXTRA["link_check"]
    e = discord.Embed(title="🧰 曲ライブラリ 管理ツール", color=discord.Color.dark_teal())
    e.add_field(name="📨 未処理のリクエスト", value=f"{pending}件", inline=True)
    e.add_field(name="🎼 プレイリスト", value=f"{len(X.EXTRA['playlists'])}個", inline=True)
    e.add_field(name="🗑️ 倉庫", value=f"{len(M.DELETED_SONGS)}曲", inline=True)
    e.add_field(name="📅 今日の一曲（自動投稿）", value=describe_daily(guild), inline=False)
    if lc.get("at"):
        e.add_field(
            name="🔗 前回のリンクチェック",
            value=f"{lc['at']}（問題あり {len(lc.get('bad', []))}曲）",
            inline=False,
        )
    e.set_footer(text="📅は、いま開いているチャンネルに投稿するよう設定されます")
    return e


class PlaylistDeleteView(discord.ui.View):
    def __init__(self, admin_id: int):
        super().__init__(timeout=120)
        self.admin_id = admin_id
        names = list(X.EXTRA["playlists"])[:25]
        sel = discord.ui.Select(
            placeholder="削除するプレイリストを選択",
            options=[
                discord.SelectOption(label=n[:100], value=n[:100], description=f"{len(X.EXTRA['playlists'][n]['ids'])}曲")
                for n in names
            ],
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        name = interaction.data["values"][0]
        # 100文字で切っているので、元の名前を前方一致で探す
        key = next((n for n in X.EXTRA["playlists"] if n[:100] == name), None)
        if key is None:
            await interaction.response.edit_message(content="すでに削除されています。", view=None)
            return
        X.EXTRA["playlists"].pop(key)
        X.save_extra()
        await interaction.response.edit_message(content=f"🗑️ プレイリスト「{key}」を削除しました。", view=None)


async def _read_export_attachment(interaction: discord.Interaction, link: str) -> bytes:
    """メッセージのリンクから、添付されているJSONファイルの中身を取り出す。"""
    m = MSG_LINK_RE.search(link)
    if not m:
        raise ValueError("メッセージのリンクを貼ってください（メッセージを長押し/右クリック →「メッセージリンクをコピー」）。")
    ch_id, msg_id = int(m.group(1)), int(m.group(2))
    client = interaction.client
    try:
        ch = client.get_channel(ch_id) or await client.fetch_channel(ch_id)
        if interaction.guild and getattr(ch, "guild", None) and ch.guild.id != interaction.guild.id:
            raise ValueError("このサーバーのメッセージのリンクを指定してください。")
        msg = await ch.fetch_message(msg_id)
    except (discord.NotFound, discord.Forbidden):
        raise ValueError("そのメッセージを読めませんでした（botがそのチャンネルを見られるか確認してください）。")
    att = next((a for a in msg.attachments if a.filename.lower().endswith(".json")), None)
    if att is None:
        raise ValueError("そのメッセージにJSONファイルが添付されていません。")
    if att.size > MAX_IMPORT_BYTES:
        raise ValueError("ファイルが大きすぎます（2MBまで）。")
    return await att.read()


class ImportModal(discord.ui.Modal, title="曲を取り込む"):
    def __init__(self):
        super().__init__()
        self.link_in = discord.ui.TextInput(
            label="書き出しファイルのメッセージリンク",
            placeholder="https://discord.com/channels/…",
            max_length=300,
        )
        self.add_item(self.link_in)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            raw = await _read_export_attachment(interaction, self.link_in.value.strip())
            added, skipped = X.import_songs(raw, interaction.user)
        except ValueError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.followup.send(
            f"📥 取り込み完了: 追加 {added}曲 / スキップ {skipped}件（URL重複・不正データ）\n"
            "※ プレイリストは曲IDが変わるため取り込まれません。",
            ephemeral=True,
        )


class AdminToolsView(discord.ui.View):
    def __init__(self, admin_id: int):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        pending = sum(1 for r in X.EXTRA["requests"] if r["status"] == "pending")

        def add(label, cb, row, style=discord.ButtonStyle.secondary):
            b = discord.ui.Button(label=label, style=style, row=row)
            b.callback = cb
            self.add_item(b)

        add("🎼 プレイリスト作成", self._playlist_create, 0, discord.ButtonStyle.primary)
        add("🗑️ プレイリスト削除", self._playlist_delete, 0)
        add(f"📨 リクエスト（{pending}）", self._requests, 0, discord.ButtonStyle.success if pending else discord.ButtonStyle.secondary)
        add("♻️ 重複チェック", self._dupes, 0)
        add("🔗 リンクチェック", self._linkcheck, 0)
        add("📦 書き出し", self._export, 1)
        add("📥 取り込み（管理者のみ）", self._import, 1)
        add("📅 毎日の自動投稿", self._daily, 1)
        add("🔙 設定に戻る", self._back, 1)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("このメニューは実行者のみ使用できます。", ephemeral=True)
            return False
        return True

    async def _playlist_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(X.PlaylistModal())

    async def _playlist_delete(self, interaction: discord.Interaction):
        if not X.EXTRA["playlists"]:
            await interaction.response.send_message("プレイリストがありません。", ephemeral=True)
            return
        await interaction.response.send_message(
            "削除するプレイリストを選んでください。", view=PlaylistDeleteView(self.admin_id), ephemeral=True
        )

    async def _requests(self, interaction: discord.Interaction):
        await X.show_requests(interaction)

    async def _dupes(self, interaction: discord.Interaction):
        await X.show_dupes(interaction)

    async def _linkcheck(self, interaction: discord.Interaction):
        await X.run_linkcheck_interaction(interaction)

    async def _export(self, interaction: discord.Interaction):
        await X.send_export(interaction)

    async def _import(self, interaction: discord.Interaction):
        if not M.is_admin(interaction.user.id):
            await interaction.response.send_message("取り込みは管理者のみ使用できます。", ephemeral=True)
            return
        await interaction.response.send_modal(ImportModal())

    async def _daily(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DailyModal(interaction.guild))

    async def _back(self, interaction: discord.Interaction):
        view = M.MusicSettingView(self.admin_id)
        await interaction.response.edit_message(embed=view.current_embed(), view=view)
