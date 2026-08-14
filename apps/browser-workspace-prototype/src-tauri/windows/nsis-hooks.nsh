!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    RmDir /r "$LOCALAPPDATA\Skkima\plugin-library"
    RmDir /r "$LOCALAPPDATA\Skkima\skill-library"
    RmDir "$LOCALAPPDATA\Skkima"
  ${EndIf}
!macroend
