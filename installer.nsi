!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "1.0.0"
!endif

!define APP_NAME      "GooseRSS"
!define APP_DISPLAY   "GooseRSS 阅读器"
!define PUBLISHER     "Minecraftgoose"
!define EXE_NAME      "GooseRSS.exe"
!define UNINST_KEY    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
!define REPO_URL      "https://github.com/Minecraftgoose/GooseRSS/"

Name "${APP_DISPLAY} ${APP_VERSION}"
OutFile "dist\${APP_NAME}-Setup-${APP_VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
SetCompressorDictSize 32
XPStyle on

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName"     "${APP_DISPLAY}"
VIAddVersionKey "CompanyName"     "${PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_DISPLAY} 安装程序"
VIAddVersionKey "FileVersion"     "${APP_VERSION}"
VIAddVersionKey "ProductVersion"  "${APP_VERSION}"
VIAddVersionKey "LegalCopyright"  "Copyright 2026 ${PUBLISHER}"

!define MUI_ABORTWARNING
!define MUI_UNABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${EXE_NAME}"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APP_DISPLAY}"
!define MUI_FINISHPAGE_LINK "访问项目主页"
!define MUI_FINISHPAGE_LINK_LOCATION "${REPO_URL}"
!define MUI_FINISHPAGE_SHOWREADME ""
!define MUI_FINISHPAGE_NOREBOOTSUPPORT

!insertmacro MUI_PAGE_LICENSE "LICENSE.utf8bom.txt"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"

Section "主程序 (必需)" SEC_MAIN
  SectionIn RO
  SetOutPath "$INSTDIR"

  File /r "dist\${APP_NAME}\*.*"

  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName"     "${APP_DISPLAY}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion"  "${APP_VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher"       "${PUBLISHER}"
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${EXE_NAME},0"
  WriteRegStr HKLM "${UNINST_KEY}" "URLInfoAbout"    "${REPO_URL}"
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINST_KEY}" "NoRepair" 1

  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" "$0"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "开始菜单快捷方式" SEC_STARTMENU
  CreateDirectory "$SMPROGRAMS\${APP_DISPLAY}"
  CreateShortCut "$SMPROGRAMS\${APP_DISPLAY}\${APP_DISPLAY}.lnk" \
    "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME},0"
  CreateShortCut "$SMPROGRAMS\${APP_DISPLAY}\卸载 ${APP_DISPLAY}.lnk" \
    "$INSTDIR\Uninstall.exe"
SectionEnd

Section "桌面快捷方式" SEC_DESKTOP
  CreateShortCut "$DESKTOP\${APP_DISPLAY}.lnk" \
    "$INSTDIR\${EXE_NAME}" "" "$INSTDIR\${EXE_NAME},0"
SectionEnd

LangString DESC_SEC_MAIN      ${LANG_SIMPCHINESE} "GooseRSS 主程序与运行库"
LangString DESC_SEC_STARTMENU ${LANG_SIMPCHINESE} "在开始菜单创建快捷方式"
LangString DESC_SEC_DESKTOP   ${LANG_SIMPCHINESE} "在桌面创建快捷方式"

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN}      $(DESC_SEC_MAIN)
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_STARTMENU} $(DESC_SEC_STARTMENU)
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_DESKTOP}   $(DESC_SEC_DESKTOP)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

Section "Uninstall"
  ExecWait 'taskkill /F /IM ${EXE_NAME} /T'
  Sleep 500

  RMDir /r "$INSTDIR"

  Delete "$SMPROGRAMS\${APP_DISPLAY}\${APP_DISPLAY}.lnk"
  Delete "$SMPROGRAMS\${APP_DISPLAY}\卸载 ${APP_DISPLAY}.lnk"
  RMDir  "$SMPROGRAMS\${APP_DISPLAY}"
  Delete "$DESKTOP\${APP_DISPLAY}.lnk"

  DeleteRegKey HKLM "${UNINST_KEY}"
SectionEnd
