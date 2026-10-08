"""Module for general configurations of the process"""

import logging
import os
import tempfile

logger = logging.getLogger(__name__)

MAX_RETRY = 10

# ----------------------
# Queue population settings
# ----------------------
MAX_CONCURRENCY = 100  # tune based on backend capacity
MAX_RETRIES = 3  # transient failure retries per item
RETRY_BASE_DELAY = 0.5  # seconds (exponential backoff)

# ----------------------
# TLS/SSL-indstillinger
# ----------------------
# Fallback-placeringer af OS'ets standard-CA-bundle til lokal Linux/Unix-
# udvikling. Windows har intet PEM-systemlager, der bruges certifi i stedet.
_SYSTEM_CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL/CentOS
    "/etc/ssl/cert.pem",  # Alpine/BSD/macOS
)


def _base_ca_bundle() -> str | None:
    """Stien til det offentlige basis-CA-bundle, ATS' CA tilføjes til.

    Foretrækker certifi's bundle, som requests stoler på på alle platforme, og
    falder tilbage til OS'ets PEM-lager.

    Returns:
        Stien til det bedste tilgængelige basis-bundle, eller None.
    """
    try:
        import certifi  # noqa: PLC0415  (valgfri base, hentes lazy)

        return certifi.where()
    except ImportError:
        pass
    for path in _SYSTEM_CA_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def apply_ca_bundle() -> str | None:
    """Stoler på ATS' CA-certifikat i tillæg til de offentlige rodcertifikater.

    Læser stien fra miljøvariablen ``AARHUS_ROOT_CERT_PEM``. Et kombineret
    bundle (basis-tillidskæden + ATS' CA) skrives til en temp-fil, og
    ``REQUESTS_CA_BUNDLE`` og ``SSL_CERT_FILE`` peger på den, så både ATS og
    offentlige endpoints som OS2Forms verificerer i samme proces. Kaldes én
    gang ved processtart, før noget HTTPS-kald.

    Returns:
        Stien til det anvendte bundle, eller None hvis variablen er tom.

    Raises:
        FileNotFoundError: Hvis variablen er sat, men filen ikke findes.
    """
    ca_bundle = os.getenv("AARHUS_ROOT_CERT_PEM", "")
    if not ca_bundle:
        return None
    if not os.path.isfile(ca_bundle):
        raise FileNotFoundError(
            f"CA bundle not found: {ca_bundle!r} (check AARHUS_ROOT_CERT_PEM)"
        )

    base = _base_ca_bundle()
    if base is None:
        logger.warning(
            "No certifi/system CA bundle found; trusting ATS CA only (%s).", ca_bundle
        )
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        os.environ["SSL_CERT_FILE"] = ca_bundle
        return ca_bundle

    with open(base, encoding="utf-8") as fh:
        combined = fh.read()
    if not combined.endswith("\n"):
        combined += "\n"
    with open(ca_bundle, encoding="utf-8") as fh:
        combined += fh.read()

    combined_path = os.path.join(tempfile.gettempdir(), "ats_combined_ca_bundle.pem")
    # Atomisk skrivning, så en samtidig læser aldrig ser et halvskrevet bundle.
    fd, tmp_path = tempfile.mkstemp(
        prefix="ats_combined_ca_bundle.", suffix=".pem", dir=tempfile.gettempdir()
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(combined)
        os.replace(tmp_path, combined_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    os.environ["REQUESTS_CA_BUNDLE"] = combined_path
    os.environ["SSL_CERT_FILE"] = combined_path
    logger.info("TLS: trusting %s + ATS CA %s -> %s", base, ca_bundle, combined_path)
    return combined_path
