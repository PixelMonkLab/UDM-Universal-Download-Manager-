# VDM – Privacy Policy

_Last updated: 26 November 2026 · applies to the VDM app and the VDM browser add-on (v1.0 Beta)_

**Short version: VDM doesn't collect, sell or send your data anywhere. Everything stays on your own computer.**

## What the add-on does with data
The VDM add-on only talks to the **VDM app running on your own computer** (address `127.0.0.1`, which never leaves your computer).

- **Links and page addresses:** when you click Download, the add-on gives the link (and the page it came from) to the VDM app so it can download the file.
- **Cookies of the site you're downloading from:** only when VDM takes over a download, the add-on passes that site's cookies to the VDM app. This lets downloads that need you to be logged in work, the same way the browser itself would download them. These cookies are only kept in memory for that download. They are never written to disk and never sent anywhere else.
- **Settings:** your choices (quality, notifications, and so on) are stored in the browser's own storage on your computer.
- **Download list:** the VDM app keeps a list of your downloads in a file on your computer, so you can resume, open or delete them.

## What VDM never does
- No accounts, no analytics, no tracking, no ads.
- No data is sent to the developer or to any third party.
- No browsing history is collected. The add-on only looks at network requests to spot video streams and downloads on the page you're on, and it forgets them when you leave the page.

## Network connections the VDM app makes
- To the websites you choose to download from.
- Once a day, to `pypi.org`, to update its download engine (yt-dlp). Only a version check and the download of the engine.

## Phone access
If you switch on "Use on phone", phones on your Wi-Fi that have the secret QR link can use VDM on your computer. It's off by default, and you can switch it off or make a new secret link at any time.

## Contact
Questions: open an issue at https://github.com/PixelMonkLab/VDM-Video-Downloads-Manager-/issues
