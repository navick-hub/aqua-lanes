# 東京アクア 空きレーン

東京アクアティクスセンター（メインプール50m／サブプール25m）の**空きレーン状況**を1画面で見られる静的サイト。

「東京都スポーツ施設予約システム」の**空き状況照会をログインなしで**スクレイプし、週間の `○空き / ×なし / −受付期間外` と、日別の空き時間帯を表示する。

- **データ源**: 東京都スポーツ施設予約システム スマホ版 `https://sports.tef.or.jp/sp/`（空き状況照会・ログイン不要）
- **表示できるもの**: 各日に「その時間帯に空きレーンがあるか（○/×）」。※予約システムは面数（レーン本数）を機械取得できる形で公開していないため、本数の数値は非対応。
- 実際の予約は[予約サイト](https://sports.tef.or.jp/)から（本サイトは閲覧専用）。

## 構成

```
aqua-lanes/
├── index.html                   # 単一ファイル（UI + 埋め込みDATA）。GitHub Pagesで公開
├── scraper.py                   # 予約システムを辿ってDATAブロックを更新
└── .github/workflows/update.yml # 6時間毎に scraper 実行 → index.html を自動commit
```

## 仕組み

`scraper.py` が以下をログインなしで辿る（すべて実証済み）:

1. `GET /sp/` … セッション取得
2. `GET rsvPTransInstSrchVacantAction` … 空き検索TOP
3. `GET rsvPTransInstSrachInstnameAction` … 施設名検索
4. `POST rsvPInstSrachInstnameBludAction`（`srchInstName=アクア`, **Shift_JIS**）… 施設一覧
5. `GET rsvPTransInstSrchDayWeekAction`（施設コードで選択）… 期間設定
6. `POST rsvPTransInstSrchVacantTzoneAction`（年月日）… 時間帯別 空き状況

施設コード（メイン `30000060` / サブ `30000460`）は毎回「アクア」検索で動的解決し、失敗時はハードコードにフォールバック。本日から `DAYS_AHEAD`（既定28）日分を取得する。

> **TLS注意**: このサーバは弱いDHパラメータのレガシーTLS。`scraper.py` は `SECLEVEL=1` に下げるアダプタで接続する（証明書検証は有効のまま）。

## ローカル実行

```bash
pip install requests
python scraper.py         # index.html の DATA ブロックを更新
python -m http.server 4182  # http://localhost:4182 で確認
```

## デプロイ

GitHubリポジトリにpushし、Settings → Pages で `main` ブランチのルートを公開。以降は Actions が6時間毎に自動更新する。
