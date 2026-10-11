"""
永続ストレージ用の共通モジュール。

Renderの無料プランはコンテナが再起動・再デプロイされるとファイルシステムが
リセットされてしまうため、ローカルのJSONファイルに保存するだけでは
/music_setting で保存した内容が消えてしまいます。

このモジュールは、環境変数 UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN
が設定されていれば、無料のUpstash Redis（外部の永続ストレージ）にJSONを
保存・読込します。Renderが再起動してもUpstash側にデータが残るので、
設定内容が吹き飛ばなくなります。

Upstashの環境変数が設定されていない場合は、従来どおりローカルのJSON
ファイルに読み書きします（ローカルでの動作確認用のフォールバック）。
"""

import json
import os
import urllib.request

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")


def upstash_enabled() -> bool:
    return bool(UPSTASH_URL and UPSTASH_TOKEN)


def _upstash_command(command: list):
    body = json.dumps(command).encode("utf-8")
    req = urllib.request.Request(UPSTASH_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {UPSTASH_TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _key_from_path(path: str) -> str:
    name = os.path.splitext(os.path.basename(path))[0]
    return f"sapiensubot:{name}"


def load_json(path: str, default):
    if upstash_enabled():
        try:
            result = _upstash_command(["GET", _key_from_path(path)])
            value = result.get("result")
            if value is None:
                return default
            return json.loads(value)
        except Exception as e:
            print(f"[storage] Upstashからの読み込みに失敗しました（{path}）: {e}")
            return default

    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    if upstash_enabled():
        try:
            payload = json.dumps(data, ensure_ascii=False)
            _upstash_command(["SET", _key_from_path(path), payload])
            return
        except Exception as e:
            print(f"[storage] Upstashへの保存に失敗しました（{path}）: {e}")
            # 通信失敗時はローカルにも保存を試みる（次回再起動までの保険）

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def claim_once(key: str, ttl: int = 60) -> bool:
    """同じ処理を複数のbotプロセスが同時にやってしまうのを防ぐための「早い者勝ち」の印。

    最初に呼んだプロセスだけ True を返し、あとから呼んだプロセスは False を返します。
    Upstash が設定されているときだけ、プロセスをまたいで効きます
    （設定されていない／通信に失敗したときは True を返し、普通に処理を続けます）。
    """
    if not upstash_enabled():
        return True
    try:
        result = _upstash_command(["SET", f"sapiensubot:claim:{key}", "1", "NX", "EX", str(ttl)])
        return result.get("result") == "OK"
    except Exception as e:
        print(f"[storage] claim_once に失敗しました（{key}）: {e}")
        return True


def load_remote_json(key: str, default, *, strict: bool = False):
    """Upstash Redis専用のJSON値を読み込みます。ローカルファイルへはフォールバックしません。

    strict=True の場合、通信/JSONエラーを default に置き換えず、そのまま呼び出し側へ返します。
    永続データを空データで上書きしてしまう事故を防ぐために使用します。
    """
    if not upstash_enabled():
        return default
    try:
        result = _upstash_command(["GET", key])
        value = result.get("result")
        if value is None:
            return default
        return json.loads(value)
    except Exception as e:
        print(f"[storage] Upstashからの読み込みに失敗しました（{key}）: {e}")
        if strict:
            raise
        return default


def save_remote_json(key: str, data) -> bool:
    """Upstash Redis専用にJSON値を保存します。成功時True。"""
    if not upstash_enabled():
        return False
    try:
        payload = json.dumps(data, ensure_ascii=False)
        result = _upstash_command(["SET", key, payload])
        return result.get("result") == "OK"
    except Exception as e:
        print(f"[storage] Upstashへの保存に失敗しました（{key}）: {e}")
        return False


# =========================================================
# ハッシュ（フィールド単位で読み書きできる保存形式）
#
# 1つのキーに大量のデータを入れると「1回の保存サイズ」の上限に当たるため、
# サーバー複製（commands/mirror.py）のように件数が多くなるデータは
# Upstash Redis の Hash（HSET / HGETALL）に1件ずつ保存します。
# ・HSET は複数フィールドを1コマンドでまとめて書き込めるので、
#   Upstash無料枠のコマンド数も節約できます。
# ・Upstash が未設定のときは、プロセス内のメモリにだけ保存します
#   （再起動すると消えます）。
# =========================================================
_local_hashes: dict = {}


def hash_set(key: str, mapping: dict) -> bool:
    """mapping（{フィールド: 文字列}）をまとめて保存する。成功時 True。"""
    if not mapping:
        return True
    if not upstash_enabled():
        _local_hashes.setdefault(key, {}).update({str(k): str(v) for k, v in mapping.items()})
        return True
    try:
        cmd = ["HSET", key]
        for k, v in mapping.items():
            cmd += [str(k), str(v)]
        result = _upstash_command(cmd)
        return "error" not in result
    except Exception as e:
        print(f"[storage] Hash保存に失敗しました（{key}）: {e}")
        return False


def hash_get_all(key: str, *, strict: bool = False) -> dict:
    """Hash の全フィールドを {フィールド: 文字列} で返す。

    strict=True のときは通信エラーを握りつぶさず例外にする
    （「空だった」と誤認して二重に処理してしまう事故を防ぐため）。
    """
    if not upstash_enabled():
        return dict(_local_hashes.get(key, {}))
    try:
        result = _upstash_command(["HGETALL", key])
        if "error" in result:
            raise RuntimeError(result["error"])
        value = result.get("result") or []
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        return {str(value[i]): str(value[i + 1]) for i in range(0, len(value) - 1, 2)}
    except Exception as e:
        print(f"[storage] Hash読み込みに失敗しました（{key}）: {e}")
        if strict:
            raise
        return {}


def hash_delete_fields(key: str, fields: list) -> bool:
    if not fields:
        return True
    if not upstash_enabled():
        h = _local_hashes.get(key, {})
        for f in fields:
            h.pop(str(f), None)
        return True
    try:
        _upstash_command(["HDEL", key] + [str(f) for f in fields])
        return True
    except Exception as e:
        print(f"[storage] Hash削除に失敗しました（{key}）: {e}")
        return False


def delete_key(key: str) -> bool:
    if not upstash_enabled():
        _local_hashes.pop(key, None)
        return True
    try:
        _upstash_command(["DEL", key])
        return True
    except Exception as e:
        print(f"[storage] キー削除に失敗しました（{key}）: {e}")
        return False
