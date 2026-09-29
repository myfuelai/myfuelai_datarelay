"""Thin wrapper around the QuickBooks SDK request processor (QBXMLRP2.RequestProcessor, COM).

Only the calls one sync run needs: OpenConnection2 -> BeginSession -> ProcessRequest* ->
EndSession -> CloseConnection. The connection is always ctLocalQBD (never LaunchUI, so QuickBooks
is never shown) and the company file is always opened qbFileOpenMultiUser - never single-user, so
the service can't lock other users out or force them to switch modes.
"""
import logging
import sys

log = logging.getLogger("relay.quickbooks")

# QBXMLRP2 enums
CT_LOCAL_QBD = 1              # ConnectionType.ctLocalQBD
QB_FILE_OPEN_MULTI_USER = 1   # QBFileMode.qbFileOpenMultiUser
QB_FILE_OPEN_DO_NOT_CARE = 2  # QBFileMode.qbFileOpenDoNotCare - `authorize` only

PROG_ID = "QBXMLRP2.RequestProcessor"
CO_E_CLASSSTRING = "0x800401F3"  # "Invalid class string" - ProgID not registered


class QBComError(RuntimeError):
    """A COM call into QuickBooks failed. `hresult` is QuickBooks' own error code when the SDK
    supplied one (e.g. 0x80040408), otherwise the COM HRESULT."""

    def __init__(self, call: str, hresult: str, message: str):
        super().__init__(f"{call} failed: HRESULT={hresult} {message}")
        self.call = call
        self.hresult = hresult
        self.message = message


def _hex(code) -> str:
    return f"0x{code & 0xFFFFFFFF:08X}" if isinstance(code, int) else str(code)


def _com_error(call: str, e) -> QBComError:
    # pywintypes.com_error args: (hresult, text, excepinfo, argerror); excepinfo[5] is the
    # server's scode - QuickBooks puts its specific error there.
    hresult, text, excepinfo = (list(getattr(e, "args", ())) + [None, None, None])[:3]
    code, message = hresult, text
    if excepinfo:
        if len(excepinfo) > 5 and excepinfo[5]:
            code = excepinfo[5]
        if len(excepinfo) > 2 and excepinfo[2]:
            message = excepinfo[2]
    return QBComError(call, _hex(code), str(message or e))


class RequestProcessor:
    def __init__(self):
        import pythoncom
        import pywintypes
        import win32com.client
        self._pythoncom = pythoncom
        self._com_error_type = pywintypes.com_error
        pythoncom.CoInitialize()
        try:
            self._rp = win32com.client.Dispatch(PROG_ID)
        except self._com_error_type as e:
            pythoncom.CoUninitialize()
            error = _com_error(f"Dispatch({PROG_ID})", e)
            if error.hresult == CO_E_CLASSSTRING:
                error = QBComError(error.call, error.hresult,
                                   f"{error.message} - the QuickBooks SDK request processor isn't registered for this "
                                   f"{'64' if sys.maxsize > 2**32 else '32'}-bit process (SDK not installed, or 32/64-bit mismatch)")
            raise error from e

    def _call(self, name, *args):
        """Calls a QBXMLRP2 method, turning COM errors into QBComError."""
        try:
            return getattr(self._rp, name)(*args)
        except self._com_error_type as e:
            raise _com_error(name, e) from e

    def open_connection(self, app_id: str, app_name: str) -> None:
        self._call("OpenConnection2", app_id, app_name, CT_LOCAL_QBD)

    def begin_session(self, company_file_path: str) -> str:
        return self._call("BeginSession", company_file_path, QB_FILE_OPEN_MULTI_USER)

    def begin_session_on_open_file(self) -> str:
        """Only for `authorize` (run by hand by a QuickBooks Admin): the company file already open in
        this Windows session's QuickBooks, in whatever mode it's open. The service never uses this."""
        return self._call("BeginSession", "", QB_FILE_OPEN_DO_NOT_CARE)

    def process_request(self, qb_ticket: str, qbxml: str) -> str:
        return self._call("ProcessRequest", qb_ticket, qbxml)

    def end_session(self, qb_ticket: str) -> None:
        self._call("EndSession", qb_ticket)

    def close_connection(self) -> None:
        self._call("CloseConnection")

    def release(self) -> None:
        """Drops the COM reference and uninitializes COM for this thread - the last step of every
        run, so nothing in this process still holds QuickBooks."""
        self._rp = None
        try:
            self._pythoncom.CoUninitialize()
        except Exception as e:
            log.warning(f"CoUninitialize failed: {e}")
