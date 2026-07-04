# Manuscript Workspace Image Saver

This Chrome extension saves generated ChatGPT images directly into a local Manuscript Workspace server.

It runs only on:

- `https://chatgpt.com/*`
- `https://chat.openai.com/*`

It posts selected image blobs only to:

- `http://127.0.0.1:8000/local/save-generated-image`

No API key is required.

## Install

1. Keep Manuscript Workspace running locally on `http://127.0.0.1:8000`.
2. Open Chrome.
3. Go to `chrome://extensions`.
4. Enable Developer mode.
5. Click Load unpacked.
6. Select this `browser-extension/` folder.

## Use

1. Generate an image in ChatGPT.
2. Click `Save to Workspace` near the upper-left corner of the generated image.
3. Enter an optional filename, chapter folder, and description.
4. The image is saved under `assets/images/` in your manuscript root.
5. Metadata is written to `assets/image-metadata.json`.

If the image cannot be fetched from the page due to browser restrictions, the extension opens the image in a new tab and shows a fallback message. In that case, download the image and use the Manuscript Workspace import-from-Downloads workflow.
