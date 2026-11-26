# UDM add-on – store submission kit

Everything needed to publish the add-on. Packages come from `python tools/package_addons.py` (folder `dist-addons/`)
or from the **Release** page on GitHub. Images are in `store/`.

Privacy policy URL (all stores ask for it):
`https://github.com/PixelMonkLab/UDM-Universal-Download-Manager-/blob/main/PRIVACY.md`

---

## 1. Firefox – one-click install without a store listing (easiest, free)
1. Make a free account at https://addons.mozilla.org/developers/
2. **Submit a New Add-on** → choose **"On your own"** (self-distribution, no public listing).
3. Upload `UDM-addon-firefox.zip`. Mozilla checks and signs it automatically (usually minutes).
4. Download the signed `.xpi` file and send it to Claude, or attach it to the GitHub Release as `UDM-addon-firefox.xpi`.
5. From then on, UDM's **Install add-on** button in Firefox installs it in one click.

## 2. Microsoft Edge Add-ons (free)
1. Register at https://partner.microsoft.com/dashboard/microsoftedge (free).
2. **Create new extension** → upload `UDM-addon-edge.zip`.
3. Fill in the texts below, upload the screenshots (`store/screenshot-*.png`), the logo (`store/logo-300.png`) and the privacy URL.
4. Review takes a few days. If Microsoft objects to YouTube, upload `UDM-addon-chrome-store.zip` instead (the same add-on without YouTube).

## 3. Chrome Web Store ($5 one-time)
1. Register at https://chrome.google.com/webstore/devconsole
2. **New item** → upload **`UDM-addon-chrome-store.zip`**. This edition has YouTube switched off, because Chrome Web Store rules don't allow YouTube downloaders. Everything else works.
3. Texts and images below. Use the promo tile `store/promo-440x280.png`.

When a store link is live, tell Claude: it goes into UDM so the Install button becomes one click for that browser.

---

## Texts

**Name:** UDM – Universal Download Manager (Beta)

**Short description (132 characters max):**
Download manager with a Download button for videos, catches streamed videos and speeds up file downloads. Works with the UDM app.

**Full description:**
UDM is a download manager for your browser and computer.

• A Download button right on Instagram and Pinterest videos (and YouTube in the full version)
• Catches streamed ("blob") videos on other websites and joins all the pieces into one MP4
• Takes over your browser downloads for faster, multi-connection downloading with pause and resume, like IDM
• Right-click any link → "Download link with UDM"
• Notifications when downloads start and finish, with Open and Show in folder
• Docks in the side panel, so your downloads are always in view
• Keyboard shortcuts: Alt+Shift+V opens UDM, Alt+Shift+D downloads the video on the page

The add-on works together with the free UDM app on your computer, which does the downloading. Download it from https://github.com/PixelMonkLab/UDM-Universal-Download-Manager-/releases/latest

Privacy: UDM collects nothing. The add-on only talks to the UDM app on your own computer.
Only download content you own or have permission to save.

**Category:** Productivity (Edge/Chrome) · Download Management (Firefox)
**Language:** English

---

## Permission reasons (the stores ask for each one)

| Permission | Why UDM needs it |
|---|---|
| `storage` | Saves your settings (quality, notifications, side panel). |
| `contextMenus` | Adds "Download video/link with UDM" to the right-click menu. |
| `notifications` | Shows "Download started" and "Download finished" messages. |
| `tabs` | Reads the current tab's address so it can be downloaded, and opens the UDM manager page. |
| `webRequest` | Spots video streams (.m3u8/.mpd) and video files that a page loads, so they can be downloaded. It only reads request addresses and never changes or blocks requests. |
| `sidePanel` | Lets UDM stay docked in the browser's side panel. |
| `downloads` | Hands browser downloads over to the faster UDM download manager, if the user turns this on (it's on by default and can be switched off). |
| `cookies` | Passes the site's login cookies to the UDM app, only for a download the user started, so downloads that need a login work. They are never stored or sent anywhere else. |
| Host access `127.0.0.1:5757` | Talks to the UDM app on the user's own computer. |
| Host access `<all_urls>` | Shows the Download button and detects videos and downloads on the sites the user visits. |

**Single purpose (Chrome):** UDM helps users download videos and files they choose, using the UDM download manager on their computer.
**Remote code:** No. All code is included in the package.
