"""Check the follow-up email settings step by step. Sends nothing.

Run from the project folder:  python scripts\\check_email.py
The password is never printed.
"""

import smtplib
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402


def main():
    host, port = Config.SMTP_HOST, Config.SMTP_PORT
    use_ssl, use_tls = Config.SMTP_USE_SSL, Config.SMTP_USE_TLS
    print("Settings the tool is using")
    print(f"  Sender:    {Config.SMTP_SENDER_EMAIL}")
    print(f"  Username:  {Config.SMTP_USERNAME}")
    print(f"  Password:  {'set (' + str(len(Config.SMTP_PASSWORD.replace(' ', ''))) + ' characters)' if Config.SMTP_PASSWORD else 'NOT SET'}")
    print(f"  Server:    {host}:{port}  SSL={use_ssl}  TLS={use_tls}")

    if Config.SMTP_PASSWORD:
        import unicodedata

        password = Config.SMTP_PASSWORD
        letters = sum(ch.isalpha() and ch.isascii() for ch in password)
        digits = sum(ch.isdigit() for ch in password)
        others = sorted({unicodedata.name(ch, "UNKNOWN") for ch in password
                         if not (ch.isascii() and ch.isalnum())})
        print(f"  Password is made of: {letters} letters, {digits} digits"
              + (f", other characters: {', '.join(others)}" if others else ""))
        if len(password) != 16 or digits or others or not password.islower():
            print("\nPROBLEM: a Gmail App password is exactly 16 lowercase letters "
                  "(no digits, symbols or capitals). This looks like a different "
                  "password, or extra characters were copied with it.")

    if use_ssl and port == 587:
        print("\nPROBLEM: port 587 needs SMTP_USE_SSL=false and SMTP_USE_TLS=true.")
    if not use_ssl and port == 465:
        print("\nPROBLEM: port 465 needs SMTP_USE_SSL=true and SMTP_USE_TLS=false.")
    if not Config.SMTP_PASSWORD:
        print("\nPROBLEM: SMTP_PASSWORD is not set in .env.")
        return

    print("\nStep 1: connect")
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        print("  OK:", server.ehlo()[0])
    except Exception as error:
        print(f"  FAILED: {type(error).__name__}: {error}")
        return

    if use_tls and not use_ssl:
        print("Step 2: start encryption (STARTTLS)")
        try:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            print("  OK")
        except Exception as error:
            print(f"  FAILED: {type(error).__name__}: {error}")
            print("  Something on this computer may be intercepting secure mail "
                  "(antivirus mail protection).")
            return

    print("Step 3: log in")
    try:
        server.login(Config.SMTP_USERNAME, Config.SMTP_PASSWORD)
        print("  OK: logged in. Email sending should work.")
    except smtplib.SMTPAuthenticationError as error:
        print(f"  FAILED: Google refused the login ({error.smtp_code}).")
        print("  Check the username is the full Gmail address and the password "
              "is the 16-letter App password, not your normal password.")
    except Exception as error:
        print(f"  FAILED: {type(error).__name__}: {error}")
    finally:
        try:
            server.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
