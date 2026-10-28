"""The country pack: brands, official domains, text patterns and where to report (domain).

The JSON files keep the Spanish keys of specs/04; aliases map them to English fields.
"""

from pydantic import BaseModel, ConfigDict, Field

from vigia.domain.knowledge import KnownCampaign


class Brand(BaseModel):
    """A brand people get impersonated as, with its verified official domains.

    Attributes:
        name: Brand name as people write it.
        domains: Registrable domains the brand really uses.
        official_channel: Where to check instead (app, site, phone line).
        kind: Sector (bank, wallet, state, carrier...).
        source_url: Official page where the domains were verified.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(alias="marca")
    domains: tuple[str, ...] = Field(alias="dominios")
    official_channel: str | None = Field(default=None, alias="canal_oficial")
    kind: str | None = Field(default=None, alias="tipo")
    source_url: str = Field(alias="fuente_url")


class ReportTo(BaseModel):
    """An official place to report a scam.

    Attributes:
        name: Who receives the report.
        url: Where to file it.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(alias="nombre")
    url: str


class PriceReference(BaseModel):
    """Usual market price of an item often sold in marketplace scams (specs/15).

    Attributes:
        item: What it is, as people write it.
        pattern: Regex (pack style) that recognizes the item in a message.
        price: Usual price in the country's currency.
        source: Where the figure comes from.
    """

    model_config = ConfigDict(frozen=True)

    item: str
    pattern: str = Field(alias="patron")
    price: float = Field(alias="precio")
    source: str = Field(alias="fuente")


class CountryPack(BaseModel):
    """Everything country-specific the signals need (specs/04).

    Attributes:
        country: Two-letter ISO code.
        language: Language of replies for businesses of this country.
        brands: Impersonated brands with their official domains.
        patterns: Regex lists by category (``urgencia``, ``pide_credenciales``...).
        price_references: Market prices for ``price_below_market`` (specs/15).
        report_to: Official reporting channels.
        campaigns: Known campaigns of the country; not in the JSON, attached by whoever
            loads the knowledge base (file in evals, table in the app). D-041.
    """

    model_config = ConfigDict(frozen=True)

    country: str = Field(alias="pais")
    language: str = Field(alias="idioma")
    brands: tuple[Brand, ...] = Field(alias="marcas")
    patterns: dict[str, tuple[str, ...]] = Field(alias="patrones")
    price_references: tuple[PriceReference, ...] = Field(default=(), alias="referencias_precio")
    report_to: tuple[ReportTo, ...] = Field(default=(), alias="reportar_a")
    campaigns: tuple[KnownCampaign, ...] = ()

    def official_domains(self) -> frozenset[str]:
        """Every official registrable domain of every brand in the pack."""
        return frozenset(d for brand in self.brands for d in brand.domains)
