"""OneDrive を Microsoft Graph API で直接スキャンする（同期フォルダが無い場合用）。

通信量削減の要点:
- delta クエリを使用。初回は全アイテムのメタデータのみ（1 件 300 バイト程度）、
  2 回目以降は deltaLink により「変更分だけ」を取得する。
- $select で必要 6 フィールドに絞り、レスポンスを最小化する。
- ファイル内容は一切ダウンロードしない。
- 取得済みメタデータとdeltaLink はキャッシュ（onedrive_cache.json）に保存し、
  再スキャン時の通信を差分のみにする。

認証: Azure CLI（az login 済みであること）。
  az account get-access-token --resource https://graph.microsoft.com
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from folder_advisor.models import FolderStat, ScanResult, name_signals, series_key
from folder_advisor.scan_local import SAMPLES_PER_DIR

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SELECT = "id,name,size,folder,file,parentReference,lastModifiedDateTime,deleted"
CACHE_FILE = "onedrive_cache.json"


class GraphAuthError(RuntimeError):
    pass


def _az_graph_token() -> str:
    """Azure CLI から Microsoft Graph 用アクセストークンを取得する。"""
    token = os.environ.get("GRAPH_ACCESS_TOKEN")
    if token:
        return token
    az = shutil.which("az")
    if not az:
        raise GraphAuthError(
            "Azure CLI (az) が見つかりません。インストールして `az login` を実行するか、"
            "環境変数 GRAPH_ACCESS_TOKEN にトークンを設定してください。"
        )
    proc = subprocess.run(
        [az, "account", "get-access-token", "--resource", "https://graph.microsoft.com",
         "--output", "json"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GraphAuthError(
            f"Azure CLI からトークンを取得できませんでした（`az login` 済みか確認してください）:\n{proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)["accessToken"]


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise GraphAuthError(f"Graph API エラー {e.code}: {body}") from e


def _item_dir_path(item: dict) -> str | None:
    """アイテムの親フォルダの相対パス（root からの "/" 区切り）。root 直下は ""。"""
    parent = item.get("parentReference") or {}
    path = parent.get("path")  # 例: "/drives/xxx/root:/A/B" / "/drive/root:"
    if path is None:
        return None  # root アイテム自身など
    _, _, rel = path.partition("root:")
    return urllib.parse.unquote(rel.lstrip("/"))


def _load_cache(cache_path: str) -> dict:
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fp:
            return json.load(fp)
    return {"delta_link": "", "items": {}}


def scan_onedrive(
    subpath: str = "",
    drive_id: str | None = None,
    cache_dir: str = "out",
    max_folders: int = 20000,
) -> ScanResult:
    """OneDrive（既定は自分のドライブ）を delta クエリでスキャンする。

    subpath を指定すると、そのフォルダ配下だけを集計対象にする
    （delta 自体はドライブ全体に対して差分取得し、クライアント側で絞り込む）。
    drive_id を指定すると SharePoint ドキュメントライブラリ等の別ドライブを対象にできる。
    """
    token = _az_graph_token()
    base = f"{GRAPH_BASE}/drives/{drive_id}" if drive_id else f"{GRAPH_BASE}/me/drive"

    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, CACHE_FILE)
    cache = _load_cache(cache_path)
    items: dict[str, dict] = cache["items"]

    url = cache.get("delta_link") or f"{base}/root/delta?$select={_SELECT}&$top=999"
    n_requests = 0
    while url:
        page = _get(url, token)
        n_requests += 1
        for item in page.get("value", []):
            if item.get("deleted"):
                items.pop(item["id"], None)
                continue
            dir_path = _item_dir_path(item)
            items[item["id"]] = {
                "name": item.get("name", ""),
                "dir": dir_path,
                "is_folder": "folder" in item,
                "size": item.get("size", 0) if "file" in item else 0,
                "mtime": (item.get("lastModifiedDateTime") or "")[:7],  # "YYYY-MM"
            }
        next_link = page.get("@odata.nextLink")
        if next_link:
            url = next_link
        else:
            cache["delta_link"] = page.get("@odata.deltaLink", "")
            url = None

    with open(cache_path, "w", encoding="utf-8") as fp:
        json.dump(cache, fp, ensure_ascii=False)

    result = _build_result(items, subpath.strip("/"), max_folders)
    result.source = f"onedrive:/{subpath.strip('/')}" + (f" (drive={drive_id})" if drive_id else "")
    result.scanned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[onedrive] Graph リクエスト {n_requests} 回（メタデータのみ・内容ダウンロードなし）")
    return result


def _build_result(items: dict[str, dict], subpath: str, max_folders: int) -> ScanResult:
    def to_rel(full: str) -> str | None:
        """subpath 基準の相対パスに変換。対象外なら None。"""
        if not subpath:
            return full
        if full == subpath:
            return ""
        if full.startswith(subpath + "/"):
            return full[len(subpath) + 1:]
        return None

    stats: dict[str, FolderStat] = {"": FolderStat(path="")}
    series: dict[str, dict[str, int]] = {}

    def ensure(rel: str) -> FolderStat:
        if rel not in stats:
            stats[rel] = FolderStat(path=rel, depth=rel.count("/") + 1 if rel else 0)
        return stats[rel]

    for it in items.values():
        if it["dir"] is None:
            continue  # ドライブの root アイテム自身
        full = f"{it['dir']}/{it['name']}" if it["dir"] else it["name"]
        if it["is_folder"]:
            rel = to_rel(full)
            if rel is not None and rel != "":
                ensure(rel)
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                ensure(parent).n_subdirs += 1
            continue
        rel_dir = to_rel(it["dir"])
        if rel_dir is None:
            continue
        fs = ensure(rel_dir)
        fs.n_files += 1
        fs.size += it["size"]
        fs.last_modified = max(fs.last_modified, it["mtime"])
        ext = os.path.splitext(it["name"])[1].lstrip(".").lower() or "(なし)"
        fs.exts[ext] = fs.exts.get(ext, 0) + 1
        if len(fs.samples) < SAMPLES_PER_DIR:
            fs.samples.append(it["name"])
        has_ver, is_wip, is_final = name_signals(it["name"])
        fs.n_versioned += has_ver
        fs.n_wip += is_wip
        fs.n_final += is_final
        key = series_key(it["name"])
        if key:
            bucket = series.setdefault(rel_dir, {})
            bucket[key] = bucket.get(key, 0) + 1

    for rel, bucket in series.items():
        stats[rel].max_series = max(bucket.values(), default=0)

    folders = sorted(stats.values(), key=lambda f: f.path)
    truncated = len(folders) > max_folders
    return ScanResult(backend="onedrive-graph", folders=folders[:max_folders], truncated=truncated)
