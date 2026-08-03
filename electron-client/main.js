"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { URL } = require("node:url");
const {
  app,
  BrowserWindow,
  dialog,
  session,
  shell,
} = require("electron");

let mainWindow = null;

function validateServerUrl(value) {
  const serverUrl = new URL(value);

  const allowedProtocols = app.isPackaged
    ? new Set(["https:"])
    : new Set(["http:", "https:"]);

  if (!allowedProtocols.has(serverUrl.protocol)) {
    throw new Error(
      app.isPackaged
        ? "The installed DSMS client requires an HTTPS server address."
        : "The DSMS server address must use HTTP or HTTPS."
    );
  }

  return serverUrl;
}

function getConfigPath() {
  if (!app.isPackaged) {
    return path.join(__dirname, "client-config.json");
  }

  const programData =
    process.env.ProgramData || app.getPath("userData");

  return path.join(
    programData,
    "DSMS",
    "client-config.json"
  );
}

function getServerUrl() {
  const environmentUrl =
    process.env.DSMS_SERVER_URL?.trim();

  if (environmentUrl) {
    return validateServerUrl(environmentUrl);
  }

  const configPath = getConfigPath();

  if (!fs.existsSync(configPath)) {
    throw new Error(
      `DSMS client configuration was not found:\n${configPath}`
    );
  }

  let config;

  try {
    config = JSON.parse(
      fs.readFileSync(configPath, "utf8")
    );
  } catch (error) {
    throw new Error(
      `The DSMS client configuration is invalid:\n${error.message}`
    );
  }

  if (
    typeof config.serverUrl !== "string" ||
    !config.serverUrl.trim()
  ) {
    throw new Error(
      "client-config.json must contain a serverUrl."
    );
  }

  return validateServerUrl(config.serverUrl.trim());
}

async function createMainWindow() {
  let serverUrl;

  try {
    serverUrl = getServerUrl();
  } catch (error) {
    dialog.showErrorBox(
      "DSMS Configuration Error",
      error.message
    );

    app.quit();
    return;
  }

  const allowedOrigin = serverUrl.origin;

  mainWindow = new BrowserWindow({
    title: "DSMS",
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 700,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      devTools: !app.isPackaged,
    },
  });

  mainWindow.webContents.on(
    "will-navigate",
    (event, destination) => {
      let destinationUrl;

      try {
        destinationUrl = new URL(destination);
      } catch {
        event.preventDefault();
        return;
      }

      if (destinationUrl.origin !== allowedOrigin) {
        event.preventDefault();
        void shell.openExternal(destinationUrl.href);
      }
    }
  );

  mainWindow.webContents.setWindowOpenHandler(
    ({ url }) => {
      let destinationUrl;

      try {
        destinationUrl = new URL(url);
      } catch {
        return { action: "deny" };
      }

      if (destinationUrl.origin === allowedOrigin) {
        return { action: "allow" };
      }

      if (
        destinationUrl.protocol === "https:" ||
        destinationUrl.protocol === "http:"
      ) {
        void shell.openExternal(destinationUrl.href);
      }

      return { action: "deny" };
    }
  );

  mainWindow.once("ready-to-show", () => {
    mainWindow?.show();
  });

  try {
    await mainWindow.loadURL(serverUrl.href);
  } catch (error) {
    dialog.showErrorBox(
      "DSMS Connection Error",
      "DSMS could not connect to the company server.\n\n" +
        "Confirm that the server and Tailscale are connected.\n\n" +
        error.message
    );
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler(
    (_webContents, _permission, callback) => {
      callback(false);
    }
  );

  await createMainWindow();

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      await createMainWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});