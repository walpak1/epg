<div align="center">

# 📺 EPG Grabber

**Automated Electronic Programme Guide (EPG) Data Collector & XMLTV Generator**

*Fast, reliable, and automated TV schedule aggregation formatted for modern IPTV players and media centers.*

<br/>

[![GitHub Action Schedule](https://img.shields.io/badge/GitHub%20Actions-Automated%20Sync-2088FF?style=for-the-badge&logo=githubactions&logoColor=white)](https://github.com)
[![XMLTV Compliant](https://img.shields.io/badge/XMLTV-Standard%20Format-4CAF50?style=for-the-badge&logo=xml&logoColor=white)](http://xmltv.org)
[![Gzip Compressed](https://img.shields.io/badge/Output-.xml%20%2B%20.xml.gz-orange?style=for-the-badge&logo=archive&logoColor=white)](https://github.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-blueviolet?style=for-the-badge)](LICENSE)
[![Status: Active](https://img.shields.io/badge/Status-Maintained-brightgreen?style=for-the-badge)](https://github.com)

</div>

---

## 📌 Overview

**EPG Grabber** is an automated pipeline engineered specifically for scraping TV broadcast schedules, processing raw programming metadata, and generating standard-compliant **XMLTV** (`.xml` and `.xml.gz`) guide feeds.

Designed for seamless integration into home theater setups, IPTV clients, and media servers, ensuring your TV schedule guide is always accurate, up-to-date, and synchronized across timezones.

---

## 🎯 Purpose & Scope

This repository is dedicated exclusively to guide metadata management:

| Capability | Scope / Purpose |
| :--- | :--- |
| 📡 **EPG Data Grabbing** | Automated fetching of program guide feeds from supported web sources. |
| ⚙️ **EPG Processing** | Parsing, cleaning, categorizing, and adjusting timezone timestamps. |
| 📄 **XMLTV Generation** | Producing clean, validated XMLTV files standard for all IPTV players. |
| 🔄 **Automated Updates** | Continuous scheduled refreshes via GitHub Actions and Cron triggers. |
| 🗂️ **Personal Data Management** | Tailored channel lineups and personalized EPG curation. |

> [!IMPORTANT]
> **No Streaming Content**: This repository **DOES NOT** provide TV streams, media servers, video feeds, or an IPTV player. It is exclusively an EPG metadata aggregator and processor.

---

## 🚀 Key Features

- ⚡ **Automated Daily Sync** — Runs on regular cron schedules to keep 24/7/365 guide data current.
- 🗜️ **Dual-Format Output** — Produces both uncompressed `epg.xml` and compressed `epg.xml.gz` (up to 90% bandwidth savings).
- 🕒 **Accurate Timezone Offsets** — Normalized program timestamps (UTC / local offsets) prevent sync drift.
- 🎨 **Rich Metadata** — Program titles, detailed descriptions, episode numbering, categories/genres, and channel logos.
- 🌐 **CDN & Cloud Ready** — Fast delivery using GitHub Pages, Cloudflare Workers, or custom web endpoints.

---

## 🔄 Architecture & Data Flow

```mermaid
flowchart LR
    A[🌐 TV Guide Sources] -->|Fetch & Parse| B(⚡ EPG Grabber Engine)
    B -->|Clean & Normalize| C{Metadata Processor}
    C -->|Format & Validate| D[📄 XMLTV Generator]
    D -->|Export Raw| E[epg.xml]
    D -->|Compress GZIP| F[epg.xml.gz]
    E & F -->|Deploy & Host| G[🚀 GitHub Pages / Worker CDN]
    G -->|Direct Feed Link| H[📺 IPTV Players & Media Centers]
```

---

## 📱 Compatible Players & Platforms

Compatible with any player, platform, or PVR client that supports standard XMLTV:

| Platform / App | Supported | Notes |
| :--- | :---: | :--- |
| **TiviMate** | ✅ | Full XMLTV & `.xml.gz` support |
| **OTT Navigator** | ✅ | Native XMLTV URL integration |
| **Kodi (IPTV Simple Client)** | ✅ | Complete EPG mapping |
| **Jellyfin / Plex / Emby** | ✅ | Live TV & DVR guide provider |
| **iMPlayer / IPTV Smarters** | ✅ | Standard EPG URL source |
| **VLC Media Player** | ✅ | Local & remote XMLTV loading |

---

## ⚖️ Disclaimer

> [!CAUTION]
> **Educational and Personal Use Only**
>
> 1. This project is provided strictly for **personal, research, and educational purposes**.
> 2. The author does not host, broadcast, or redistribute any copyright-protected audio or video streams.
> 3. Users are solely responsible for ensuring their use of EPG data complies with the terms of service of the respective data sources, local regulations, and applicable copyright laws.

---

<div align="center">

Made with ❤️ for clean TV guide management

<sub>⭐ Star this repository if you find it helpful!</sub>

</div>
