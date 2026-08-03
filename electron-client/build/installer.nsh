!include "LogicLib.nsh"
!include "nsDialogs.nsh"

Var DsmsTailscaleKey
Var DsmsTailscaleKeyInput
Var DsmsTailscaleKeyPage


!macro customHeader
  Page custom DsmsCreateTailscaleKeyPage DsmsLeaveTailscaleKeyPage
!macroend


Function DsmsCreateTailscaleKeyPage
  ; Automatic Electron updates must never request another Tailscale key.
  ${If} ${isUpdated}
    Abort
  ${EndIf}

  nsDialogs::Create 1018
  Pop $DsmsTailscaleKeyPage

  ${If} $DsmsTailscaleKeyPage == error
    Abort
  ${EndIf}

  !insertmacro MUI_HEADER_TEXT \
    "Connect DSMS securely" \
    "Enter the one-time Tailscale setup key supplied by your administrator."

  ${NSD_CreateLabel} \
    0 \
    0 \
    100% \
    30u \
    "DSMS uses Tailscale to securely connect this computer to the company server."

  Pop $0

  ${NSD_CreateLabel} \
    0 \
    38u \
    100% \
    22u \
    "Paste the one-time setup key below:"

  Pop $0

  ${NSD_CreatePassword} \
    0 \
    64u \
    100% \
    13u \
    ""

  Pop $DsmsTailscaleKeyInput

  ${NSD_CreateLabel} \
    0 \
    86u \
    100% \
    34u \
    "The key is hidden while entered and will be deleted immediately after setup. Each one-time key can be used for only one computer."

  Pop $0

  ${NSD_CreateLabel} \
    0 \
    126u \
    100% \
    34u \
    "Tailscale will be installed automatically if it is not already installed. Windows may request administrator approval."

  Pop $0

  nsDialogs::Show
FunctionEnd


Function DsmsLeaveTailscaleKeyPage
  ${NSD_GetText} \
    $DsmsTailscaleKeyInput \
    $DsmsTailscaleKey

  ${If} $DsmsTailscaleKey == ""
    MessageBox \
      MB_OK|MB_ICONEXCLAMATION \
      "Enter the one-time Tailscale setup key before continuing."

    Abort
  ${EndIf}

  StrCpy $0 $DsmsTailscaleKey 6

  ${If} $0 != "tskey-"
    MessageBox \
      MB_OK|MB_ICONEXCLAMATION \
      "The setup key does not appear to be valid. It must begin with tskey-."

    Abort
  ${EndIf}
FunctionEnd


!macro customInstall
  ; Run Tailscale provisioning only during a fresh manual installation.
  ; Do not run it during Electron automatic updates.
  ${IfNot} ${isUpdated}
    DetailPrint "Preparing secure DSMS network access..."

    SetOutPath "$PLUGINSDIR"

    File \
      /oname=$PLUGINSDIR\provision-tailscale.ps1 \
      "${BUILD_RESOURCES_DIR}\provision-tailscale.ps1"

    StrCpy $0 "$PLUGINSDIR\dsms-tailscale-auth-key.txt"

    ClearErrors

    FileOpen $1 "$0" w

    ${If} ${Errors}
      MessageBox \
        MB_OK|MB_ICONSTOP \
        "DSMS could not create the temporary Tailscale key file."

      Abort
    ${EndIf}

    FileWrite $1 "$DsmsTailscaleKey"
    FileClose $1

    DetailPrint "Installing and configuring Tailscale..."

    nsExec::ExecToLog \
      'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\provision-tailscale.ps1" -AuthKeyFile "$0"'

    Pop $2

    ; Delete the temporary plaintext key immediately.
    Delete "$0"

    ; Remove the key from the installer variable as soon as possible.
    StrCpy $DsmsTailscaleKey ""

    ${If} $2 != 0
      MessageBox \
        MB_OK|MB_ICONSTOP \
        "Tailscale setup failed.$\r$\n$\r$\nDSMS installation cannot continue securely.$\r$\n$\r$\nError code: $2"

      Abort
    ${EndIf}

    DetailPrint "Tailscale setup completed successfully."
  ${EndIf}
!macroend