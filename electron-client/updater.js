"use strict";

const { app, dialog } = require("electron");
const { autoUpdater } = require("electron-updater");

let promptIsOpen = false;
let userRequestedDownload = false;

function getUsableWindow(getMainWindow) {
  const window = getMainWindow();

  if (!window || window.isDestroyed()) {
    return null;
  }

  return window;
}

async function showMessage(getMainWindow, options) {
  const window = getUsableWindow(getMainWindow);

  if (window) {
    return dialog.showMessageBox(window, options);
  }

  return dialog.showMessageBox(options);
}

function clearProgress(getMainWindow) {
  const window = getUsableWindow(getMainWindow);

  if (window) {
    window.setProgressBar(-1);
  }
}

function setupAutoUpdater(getMainWindow) {
  // Updates work only in the installed, packaged application.
  if (!app.isPackaged || process.platform !== "win32") {
    console.log(
      "DSMS update checking is disabled in development mode."
    );
    return;
  }

  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.autoRunAppAfterInstall = true;
  autoUpdater.allowPrerelease = false;

  // DSMS uses the full NSIS installer, not nsis-web.
  autoUpdater.disableWebInstaller = true;
  autoUpdater.logger = console;

  autoUpdater.on("update-available", async (info) => {
    if (promptIsOpen) {
      return;
    }

    promptIsOpen = true;

    try {
      const result = await showMessage(getMainWindow, {
        type: "info",
        title: "DSMS Update Available",
        message: `DSMS ${info.version} is available.`,
        detail:
          `You are currently using DSMS ${app.getVersion()}.\n\n` +
          "Update now will download the update, restart DSMS, " +
          "and install it.\n\n" +
          "Update next time will continue opening DSMS normally.",
        buttons: [
          "Update now",
          "Update next time",
        ],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      });

      if (result.response !== 0) {
        return;
      }

      userRequestedDownload = true;

      const window = getUsableWindow(getMainWindow);

      if (window) {
        // Indeterminate progress until percentage information arrives.
        window.setProgressBar(2);
      }

      await autoUpdater.downloadUpdate();
    } catch (error) {
      clearProgress(getMainWindow);

      dialog.showErrorBox(
        "DSMS Update Error",
        "The DSMS update could not be downloaded.\n\n" +
          error.message
      );
    } finally {
      promptIsOpen = false;
    }
  });

  autoUpdater.on("update-not-available", () => {
    console.log("DSMS is up to date.");
  });

  autoUpdater.on("download-progress", (progress) => {
    const window = getUsableWindow(getMainWindow);

    if (window) {
      const percentage = Math.max(
        0,
        Math.min(1, progress.percent / 100)
      );

      window.setProgressBar(percentage);
    }
  });

  autoUpdater.on("update-downloaded", () => {
    clearProgress(getMainWindow);

    if (!userRequestedDownload) {
      return;
    }

    // Update now was selected, so close, install, and reopen DSMS.
    setImmediate(() => {
      autoUpdater.quitAndInstall(false, true);
    });
  });

  autoUpdater.on("error", (error) => {
    console.error("DSMS updater error:", error);

    clearProgress(getMainWindow);

    // Do not bother users when a background check fails.
    // Show an error only after they intentionally selected Update now.
    if (userRequestedDownload) {
      dialog.showErrorBox(
        "DSMS Update Error",
        "The DSMS update could not be completed.\n\n" +
          error.message
      );

      userRequestedDownload = false;
    }
  });

  // Delay the startup check so the DSMS window can finish opening.
  setTimeout(() => {
    autoUpdater.checkForUpdates().catch((error) => {
      console.error(
        "DSMS update check failed:",
        error
      );
    });
  }, 8000);
}

module.exports = {
  setupAutoUpdater,
};