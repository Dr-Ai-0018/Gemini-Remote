// MV3 service worker. The Relay connection lands here later; for now it only
// reports which extension build is actually running.
'use strict';

const RUNTIME_VERSION = chrome.runtime.getManifest().version;

chrome.runtime.onInstalled.addListener(() => {
  console.info('[gemini-remote] extension', RUNTIME_VERSION, 'installed/reloaded');
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === 'runtime_version') {
    sendResponse({ version: RUNTIME_VERSION });
  }
});
