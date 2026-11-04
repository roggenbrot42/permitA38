from permita38.core.models import PersonalTax
from permita38.core.repository import Repository
from permita38.core.settings import effective_personal_tax

PERSONAL_TAX_KEY = "personal_tax"
NOT_SAVED_WARNING = (
    "No personal tax profile saved; the estimate uses the defaults (single, public health insurance, no children)."
)


def advisory_warnings(profile: dict) -> list[str]:
    """Hints on a profile that looks inconsistent. They never block saving and are never stored."""
    out: list[str] = []
    emp = profile.get("employment") or {}
    if emp.get("steuerklasse") in (3, 4, 5) and profile.get("assessment") == "single":
        out.append("Steuerklasse III/IV/V means married; check the assessment.")
    if emp and emp.get("sv_liable") and (profile.get("unemployment") or {}).get("mode", "none") != "none":
        out.append("Voluntary unemployment insurance is usually not possible next to an employment that is insured.")
    health = profile.get("health") or {}
    if health.get("mode") == "pkv" and not health.get("pkv_basis_monthly") and (health.get("pkv_monthly") or 0) > 0:
        out.append(
            "Enter the basic-cover share from your insurer's annual statement; "
            "without it nothing of the premium counts as basic cover."
        )
    return out


class PersonalTaxService:
    def __init__(self, store: Repository):
        self.store = store

    def effective(self) -> dict:
        return effective_personal_tax(self.store.settings())

    def get(self) -> dict:
        """The stored profile, or the defaults with ``stored: false``."""
        stored = self.store.settings().get(PERSONAL_TAX_KEY) is not None
        return PersonalTax(**self.effective(), stored=stored).model_dump()

    def update(self, data: dict) -> dict:
        """Replace the stored profile (no merge) with the validated body; returns it with the advisory warnings."""
        profile = {k: v for k, v in data.items() if k not in ("stored", "warnings")}
        raw = self.store.settings()
        raw[PERSONAL_TAX_KEY] = profile
        self.store.save_settings(raw)
        return PersonalTax(
            **effective_personal_tax(raw), stored=True, warnings=advisory_warnings(effective_personal_tax(raw))
        ).model_dump()
