# Skkima Chrome Bridge

Skkima Chrome Bridge is a read-only Chrome side panel extension. It reads the active web page only after the user presses the read button, then sends the captured context to the local Skkima desktop app.

## Install

1. Start the Skkima desktop app.
2. Open `chrome://extensions` in Chrome.
3. Turn on **Developer mode**.
4. Select **Load unpacked**.
5. Choose this `chrome-extension` folder.
6. Pin **Skkima Chrome Bridge** and open it from the Chrome toolbar.

## Use

1. Open a normal `http` or `https` page in Chrome.
2. Open the Skkima Bridge side panel.
3. Keep the bridge toggle ON.
4. Select **현재 페이지 읽기**.
5. Review the page title, URL, headings, links, and optional body excerpt.
6. Select **Skkima로 보내기** when the result is appropriate.

The local bridge endpoint is `http://127.0.0.1:3217` by default. It can be changed in **연결 설정**.

## Safety scope

- Read-only page capture only.
- No click, text input, form submission, login, password, or cookie capture.
- Only `http` and `https` pages are accepted.
- The bridge accepts local requests on loopback only.
- The page body is excluded unless the user explicitly enables the body summary option.

This extension is the browser adapter layer. MCP and agent execution are separate layers and are not enabled by this extension alone.
