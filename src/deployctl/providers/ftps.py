"""FTPS (FTP over TLS/SSL) deployment provider.

Inherits from FTPProvider and secures control + data channel via TLS.
"""

from __future__ import annotations

from ftplib import FTP_TLS

from deployctl.providers.ftp import FTPProvider


class FTPSProvider(FTPProvider):
    """FTPS (FTP over TLS) Provider."""

    def connect(self) -> None:
        if self.ftp:
            return

        port = self.port or 21
        tls_ftp = FTP_TLS(timeout=self.timeout)
        tls_ftp.connect(self.host, port)
        tls_ftp.auth()
        tls_ftp.login(self.username, self.password or "")
        # Secure the data connection
        tls_ftp.prot_p()
        tls_ftp.set_pasv(True)
        self.ftp = tls_ftp

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.connect()
            welcome = self.ftp.getwelcome() if self.ftp else "Connected"
            return True, f"Successfully connected to FTPS (TLS) {self.host}:{self.port or 21}. {welcome[:60]}"
        except Exception as e:
            return False, f"FTPS connection error: {str(e)}"
        finally:
            self.close()
