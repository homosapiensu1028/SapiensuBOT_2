# SapiensuBOT_2

## 構成
- `bot_core/bot.py` : botの本体（`bot` を定義し、起動時に `commands/` を読み込む）。`/ping` とRender用のWebサーバーもここ。
- `bot_core/storage.py` : データの保存（Upstash Redis があればそこへ、なければローカルJSONへ）。
- `commands/music.py` : 曲ライブラリ本体（`/music` `/music_setting`）。
- `commands/music_extra.py` : `/music` の追加機能。
- `commands/daily.py` : 毎日の「今日の一曲」自動投稿。

## 曲ライブラリ（/music）
- `/music [genre] [count:1〜5] [equal] [vc]` : ランダム紹介。返信のボタンは 🔁もう1曲 / 🎯同ジャンル / ⭐お気に入り / 👍
- `/nowplaying` `/music_search` `/bgm` `/favorite` `/music_ranking` `/music_gacha` `/music_profile` `/music_request` `/quiz_adder` `/quiz_ranking`
- 下級管理者: `/music_setting` `/bgm_edit` `/bgm_delete` `/music_requests` `/music_export` `/music_dupes` `/music_linkcheck` `/daily_setting`
- 管理者: `/music_import`
- 管理者・下級管理者のIDは `commands/music.py` の `SUB_ADMIN_IDS` / `ADMIN_IDS` を書き換えてください。

## 再起動でデータが消える問題について
Renderの無料プランは再起動・再デプロイでファイルが消えるため、無料の外部ストレージ「Upstash Redis」に保存できます。
1. https://upstash.com で無料アカウントを作成し、Redis Databaseを1つ作成する
2. 「REST API」セクションの `UPSTASH_REDIS_REST_URL` と `UPSTASH_REDIS_REST_TOKEN` をコピーする
3. Renderの Environment に同じ名前の環境変数として追加して再デプロイする

起動ログに `Upstash Redisを永続ストレージとして使用します` と出ていればOKです。
