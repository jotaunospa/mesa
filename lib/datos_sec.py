# Copiado de jotaunospa/faro (datos_sec.py) el 2026-11-05. No editar aquí sin anotar el cambio:
# Faro y la Mesa tienen mandatos distintos y este archivo puede divergir a propósito.
# -*- coding: utf-8 -*-
"""
datos_sec.py — capa de datos fundamentales desde SEC XBRL, con fecha de publicación.

Por qué existe: Yahoo entrega el valor de hoy sin decir cuándo se supo. Sin eso,
cualquier medición hacia atrás tiene sesgo de anticipación. La SEC publica cada
hecho con su 'filed', y eso permite preguntar "qué se sabía el día X".

Reglas del mandato que este módulo respeta y que NO se pueden relajar:
  · Regla 2 — "no sé" es una respuesta válida. Un campo ausente se declara
    ausente. Nunca cero, nunca un valor inventado, nunca un proxy.
  · Regla 9 — ningún supuesto se presenta como dato. Si un número viene de
    caché vieja porque la red falló, viene marcado como rancio.

Contrato:
    fundamentales(ticker, a_fecha=None) -> Fundamentales | None
"""

import os
import io
import json
import gzip
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0.0"

# La SEC exige un User-Agent con contacto real. Un correo inventado es motivo
# de bloqueo, no un detalle de estilo.
CONTACTO = os.environ.get("FARO_CONTACTO", "contacto-en-secreto-SEC_CONTACTO")
USER_AGENT = f"Faro/{VERSION} ({CONTACTO})"

CACHE_DIR = os.environ.get(
    "FARO_SEC_CACHE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sec_cache"),
)

# Días que una caché se considera fresca sin ni siquiera preguntar a la SEC.
# Las empresas reportan cada 90 días; consultar /submissions para los 150
# tickers todas las semanas es gastar 150 llamadas para descubrir que nada
# cambió.
FRESCURA_DIAS = 7
# Desde acá, aunque no haya red, seguimos sirviendo la caché pero avisando.
RANCIO_DIAS = 100

_PAUSA = 0.12          # ~8 req/s, bajo el límite de 10 de la SEC
_TIMEOUT = 20

FORMULARIOS_ANUALES = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


# ─────────────────────────────────────────────────────────────────────────────
# Conceptos XBRL
#
# Cada campo es una cascada: se prueban en orden y gana el primero que exista.
# El orden importa — el concepto más específico va primero.
# ─────────────────────────────────────────────────────────────────────────────

CONCEPTOS: Dict[str, Dict[str, List[str]]] = {
    "utilidad_neta": {
        "us-gaap": ["NetIncomeLoss", "ProfitLoss",
                    "NetIncomeLossAvailableToCommonStockholdersBasic"],
        "ifrs-full": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    },
    "resultado_operacional": {
        # Base honesta del EBITDA. Sin esto no hay EBITDA: no se inventa.
        "us-gaap": ["OperatingIncomeLoss",
                    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
        "ifrs-full": ["ProfitLossFromOperatingActivities"],
    },
    "depreciacion": {
        "us-gaap": ["DepreciationDepletionAndAmortization",
                    "DepreciationAmortizationAndAccretionNet",
                    "DepreciationAndAmortization",
                    "UtilitiesOperatingExpenseDepreciationAndAmortization",
                    "CostOfServicesDepreciationAndAmortization",
                    "Depreciation"],
        "ifrs-full": ["DepreciationAndAmortisationExpense",
                      "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss"],
    },
    "flujo_operativo": {
        "us-gaap": ["NetCashProvidedByUsedInOperatingActivities",
                    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
        "ifrs-full": ["CashFlowsFromUsedInOperatingActivities"],
    },
    "capex": {
        "us-gaap": ["PaymentsToAcquirePropertyPlantAndEquipment",
                    "PaymentsToAcquireProductiveAssets",
                    "PaymentsToAcquireRealEstate",
                    "PaymentsToAcquireCapitalAssets"],
        "ifrs-full": ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                      "PaymentsForPropertyPlantAndEquipment"],
    },
    "dividendos_pagados": {
        "us-gaap": ["PaymentsOfDividends",
                    "PaymentsOfDividendsCommonStock",
                    "PaymentsOfOrdinaryDividends"],
        "ifrs-full": ["DividendsPaidClassifiedAsFinancingActivities", "DividendsPaid"],
    },
    "deuda_total": {
        # Orden deliberado. 'LongTermDebt' a secas es ambiguo: NNN lo usa para
        # UN pagaré emitido después del cierre (200 M) mientras su deuda real
        # son 4.472 M en 'NotesPayable'. Va después de los conceptos que sí
        # significan "total", y el ancla de período lo descarta cuando no
        # corresponde a la fecha del balance.
        "us-gaap": ["DebtLongtermAndShorttermCombinedAmount",
                    "LongTermDebtAndCapitalLeaseObligations",
                    "LongTermDebt",
                    "NotesPayable",
                    "LongTermDebtNoncurrent"],
        "ifrs-full": ["Borrowings", "NoncurrentBorrowings"],
    },
    "deuda_corriente": {
        "us-gaap": ["LongTermDebtCurrent"],
        "ifrs-full": ["CurrentBorrowings"],
    },
    "deuda_corto_plazo": {
        "us-gaap": ["ShortTermBorrowings", "ShortTermNonBankLoansAndNotesPayable"],
        "ifrs-full": ["ShorttermBorrowings"],
    },
    "arriendo_financiero_no_corriente": {
        "us-gaap": ["FinanceLeaseLiabilityNoncurrent"],
        "ifrs-full": [],
    },
    "arriendo_financiero_corriente": {
        "us-gaap": ["FinanceLeaseLiabilityCurrent"],
        "ifrs-full": [],
    },
    "arriendo_no_corriente": {
        "us-gaap": ["OperatingLeaseLiabilityNoncurrent"],
        "ifrs-full": ["NoncurrentLeaseLiabilities"],
    },
    "arriendo_corriente": {
        "us-gaap": ["OperatingLeaseLiabilityCurrent"],
        "ifrs-full": ["CurrentLeaseLiabilities"],
    },
    # ── aseguradoras ────────────────────────────────────────────────────────
    # Dos familias de conceptos que NO se pueden mezclar: el bloque suplementario
    # de P&C usa una base de primas (solo daños) y los conceptos generales otra
    # (incluye vida). Chubb: 45.790 M contra 53.014 M. Cruzarlas da un ratio
    # falso. Se toma una familia completa o ninguna.
    "primas_pc": {
        "us-gaap": ["SupplementalInformationForPropertyCasualtyInsuranceUnderwritersEarnedPremiums"],
        "ifrs-full": [],
    },
    "siniestros_pc_actual": {
        "us-gaap": ["SupplementalInformationForPropertyCasualtyInsuranceUnderwritersCurrentYearClaimsAndClaimsAdjustmentExpense"],
        "ifrs-full": [],
    },
    "siniestros_pc_previo": {
        # desarrollo de años anteriores: negativo = favorable, y hay que sumarlo
        "us-gaap": ["SupplementalInformationForPropertyCasualtyInsuranceUnderwritersPriorYearClaimsAndClaimsAdjustmentExpense"],
        "ifrs-full": [],
    },
    "adquisicion_pc": {
        "us-gaap": ["SupplementalInformationForPropertyCasualtyInsuranceUnderwritersAmortizationOfDeferredPolicyAcquisitionCosts"],
        "ifrs-full": [],
    },
    "primas_devengadas": {
        "us-gaap": ["PremiumsEarnedNet", "SupplementaryInsuranceInformationPremiumRevenue"],
        "ifrs-full": ["InsuranceRevenue"],
    },
    "siniestros_incurridos": {
        "us-gaap": ["PolicyholderBenefitsAndClaimsIncurredNet",
                    "SupplementaryInsuranceInformationBenefitsClaimsLossesAndSettlementExpense"],
        "ifrs-full": ["InsuranceServiceExpenses"],
    },
    "gastos_adquisicion": {
        "us-gaap": ["DeferredPolicyAcquisitionCostAmortizationExpense",
                    "SupplementaryInsuranceInformationAmortizationOfDeferredPolicyAcquisitionCosts"],
        "ifrs-full": [],
    },
    "reservas_siniestros": {
        "us-gaap": ["LiabilityForClaimsAndClaimsAdjustmentExpense",
                    "LiabilityForUnpaidClaimsAndClaimsAdjustmentExpenseNet"],
        "ifrs-full": [],
    },
    "resultado_antes_impuestos": {
        "us-gaap": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
        "ifrs-full": ["ProfitLossBeforeTax"],
    },
    "intereses": {
        # 'InterestIncomeExpenseNet' queda FUERA a propósito: es un neto y el
        # signo depende de la empresa (MGEE lo reporta como -33,8 M). Un
        # concepto cuyo signo no se puede afirmar no sirve para sumar.
        "us-gaap": ["InterestExpense", "InterestExpenseOperating",
                    "InterestExpenseNonoperating", "InterestExpenseDebt"],
        "ifrs-full": ["FinanceCosts", "InterestExpense"],
    },
    "impuestos": {
        "us-gaap": ["IncomeTaxExpenseBenefit"],
        "ifrs-full": ["IncomeTaxExpenseContinuingOperations"],
    },
    "caja": {
        "us-gaap": ["CashAndCashEquivalentsAtCarryingValue",
                    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
        "ifrs-full": ["CashAndCashEquivalents"],
    },
    "patrimonio": {
        "us-gaap": ["StockholdersEquity",
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
        "ifrs-full": ["Equity", "EquityAttributableToOwnersOfParent"],
    },
    "acciones": {
        # Promedio ponderado del ejercicio. Es lo correcto para utilidad por
        # acción, pero DISTORSIONA la dilución en el año de una fusión: Six
        # Flags pasó de 75,3 a 100,7 millones entre 2024 y 2025 sin emitir casi
        # nada — 2024 fue medio año de la entidad fusionada.
        "us-gaap": ["WeightedAverageNumberOfDilutedSharesOutstanding",
                    "WeightedAverageNumberOfSharesOutstandingBasic"],
        "ifrs-full": ["WeightedAverageShares"],
    },
    "acciones_circulacion": {
        # Conteo a una fecha. Es el que corresponde para medir dilución.
        # 'CommonStockSharesIssued' queda FUERA: no se mueve con las recompras,
        # porque el papel recomprado va a tesorería y sigue "emitido". KO lleva
        # años en 7.040 millones emitidas mientras las EN CIRCULACIÓN bajan.
        # El conteo bueno está en la portada del 10-K (dei), abajo.
        "us-gaap": ["CommonStockSharesOutstanding"],
        "ifrs-full": ["NumberOfSharesOutstanding"],
    },
}

# Campos que se miden en acciones, no en plata. Importa para elegir la unidad.
CAMPOS_ACCIONES = {"acciones", "acciones_circulacion"}


# ─────────────────────────────────────────────────────────────────────────────
# Estructuras
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Hecho:
    """Un número con su procedencia completa. Sin procedencia no se guarda."""
    valor: float
    concepto: str          # concepto XBRL exacto
    unidad: str            # USD, CLP, shares...
    formulario: str        # 10-K, 20-F, 10-Q...
    publicado: str         # 'filed' — la fecha en que esto se supo
    periodo_fin: str       # 'end'
    anio_fiscal: Optional[int]

    def dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Fundamentales:
    ticker: str
    cik: str
    nombre: str
    taxonomia: str                       # us-gaap | ifrs-full
    a_fecha: str
    campos: Dict[str, Hecho] = field(default_factory=dict)
    previos: Dict[str, Hecho] = field(default_factory=dict)   # mismo campo, año anterior
    ausentes: List[str] = field(default_factory=list)
    # Procedencia del dato, no del número:
    periodo: Optional[str] = None        # cierre del ejercicio de referencia
    periodo_previo: Optional[str] = None
    origen: str = "red"                  # red | cache | cache_rancio
    edad_dias: Optional[float] = None
    aviso: Optional[str] = None          # por qué está rancio o desactualizado
    declaracion_mas_nueva: Optional[Dict[str, Any]] = None

    # ── accesos ──
    def val(self, campo: str) -> Optional[float]:
        h = self.campos.get(campo)
        return h.valor if h else None

    def val_previo(self, campo: str) -> Optional[float]:
        h = self.previos.get(campo)
        return h.valor if h else None

    def unidad(self, campo: str) -> Optional[str]:
        h = self.campos.get(campo)
        return h.unidad if h else None

    @property
    def rancio(self) -> bool:
        return self.origen == "cache_rancio"

    @property
    def moneda(self) -> Optional[str]:
        """La moneda en que reporta. Si no es USD, comparar contra un precio en
        USD produce basura y quien llame tiene que saberlo."""
        for c, h in self.campos.items():
            if c not in CAMPOS_ACCIONES and h.unidad:
                return h.unidad
        return None

    # ── derivadas: todas devuelven None antes que inventar ──
    def dilucion_pct(self) -> Optional[float]:
        """Variación del número de acciones entre ejercicios.

        Prefiere el conteo EN CIRCULACIÓN a una fecha. El promedio ponderado
        es lo correcto para utilidad por acción y lo equivocado para esto: en
        el año de una fusión salta sin que la empresa haya emitido nada
        (Six Flags: 75,3 -> 100,7 millones con el mismo papel en la calle).
        """
        for campo in ("acciones_circulacion", "acciones"):
            act, prev = self.val(campo), self.val_previo(campo)
            if act and prev and act > 0 and prev > 0:   # los ceros etiquetados no cuentan
                return round((act - prev) / prev * 100.0, 2)
        return None

    def cambio_patrimonio_pct(self) -> Optional[float]:
        a, p = self.val("patrimonio"), self.val_previo("patrimonio")
        if a is None or p is None or p == 0:
            return None
        return round((a - p) / abs(p) * 100.0, 2)

    def split_probable(self) -> bool:
        """Distingue un split de una emisión real.

        Un split no cambia nada: juntan o parten el papel y el patrimonio
        queda igual. Una emisión de verdad entra plata, y el patrimonio se
        mueve. Apollomics apareció con -98,8% de "recompra" —imposible— y es
        un split inverso; Gloo con +192% y el patrimonio saltando de -345 M a
        +135 M es emisión real.

        Sin dato de patrimonio no se puede descartar, así que se marca igual:
        más vale mandarte a verificar que afirmar de más.
        """
        d = self.dilucion_pct()
        if d is None or abs(d) < 40:
            return False
        e = self.cambio_patrimonio_pct()
        if e is None:
            return True
        return abs(e) < 10

    def dilucion_base(self) -> Optional[str]:
        for campo, etiqueta in (("acciones_circulacion", "acciones en circulación (portada)"),
                                ("acciones", "promedio ponderado diluido")):
            act, prev = self.val(campo), self.val_previo(campo)
            if act and prev and act > 0 and prev > 0:
                return etiqueta
        return None

    def fcf(self) -> Optional[float]:
        op, cx = self.val("flujo_operativo"), self.val("capex")
        if op is None or cx is None:
            return None
        return op - abs(cx)

    def ebitda(self) -> Optional[float]:
        """Dos caminos, ambos son la definición estándar calculada con números
        reportados. Si ninguno se puede completar, NO hay EBITDA.

        NO se estima desde la utilidad neta con un multiplicador: eso es
        inventar un número y ponerle cara de dato.
        """
        da = self.val("depreciacion")
        if da is None:
            return None
        ro = self.val("resultado_operacional")
        if ro is not None:
            return ro + abs(da)
        # Realty Income, por ejemplo, no etiqueta resultado operacional.
        ai, it = self.val("resultado_antes_impuestos"), self.val("intereses")
        if ai is not None and it is not None:
            return ai + abs(it) + abs(da)
        return None

    def ebitda_origen(self) -> Optional[str]:
        if self.val("depreciacion") is None:
            return None
        if self.val("resultado_operacional") is not None:
            return "resultado operacional + D&A"
        if self.val("resultado_antes_impuestos") is not None and self.val("intereses") is not None:
            return "resultado antes de impuestos + intereses + D&A"
        return None

    def deuda_neta(self) -> Optional[float]:
        d, c = self.val("deuda_total"), self.val("caja")
        if d is None or c is None:
            return None
        return d - c

    def deuda_ebitda(self) -> Optional[float]:
        dn, eb = self.deuda_neta(), self.ebitda()
        if dn is None or eb is None or eb <= 0:
            return None
        return round(dn / eb, 2)

    def ffo(self) -> Optional[float]:
        """FFO = utilidad neta + depreciación y amortización."""
        ni, da = self.val("utilidad_neta"), self.val("depreciacion")
        if ni is None or da is None:
            return None
        return ni + abs(da)

    def payout_utilidad_pct(self) -> Optional[float]:
        ni, dv = self.val("utilidad_neta"), self.val("dividendos_pagados")
        if ni is None or dv is None or ni <= 0:
            return None
        return round(abs(dv) / ni * 100.0, 2)

    def payout_ffo_pct(self) -> Optional[float]:
        f, dv = self.ffo(), self.val("dividendos_pagados")
        if f is None or dv is None or f <= 0:
            return None
        return round(abs(dv) / f * 100.0, 2)

    # ── aseguradoras ──
    def _familia_seguro(self):
        """Devuelve (primas, siniestros, adquisicion, etiqueta) de UNA familia."""
        p = self.val("primas_pc")
        s_act = self.val("siniestros_pc_actual")
        if p and p > 0 and s_act is not None:
            s_prev = self.val("siniestros_pc_previo") or 0.0
            return p, s_act + s_prev, self.val("adquisicion_pc"), "suplemento P&C"
        p = self.val("primas_devengadas")
        s = self.val("siniestros_incurridos")
        if p and p > 0 and s is not None:
            return p, s, self.val("gastos_adquisicion"), "conceptos generales"
        return None, None, None, None

    def siniestralidad_pct(self) -> Optional[float]:
        """Siniestros incurridos sobre primas devengadas. Exacto y comparable."""
        p, s, _, _ = self._familia_seguro()
        if p is None or s is None:
            return None
        return round(s / p * 100.0, 2)

    def ratio_combinado_piso_pct(self) -> Optional[float]:
        """PISO del ratio combinado: siniestralidad + gastos de adquisición.

        NO es el ratio combinado. Le faltan los gastos administrativos de
        suscripción, que ninguna aseguradora etiqueta por separado en XBRL.
        En Chubb el piso da 77% y el combinado real ronda 86-87%; en Cincinnati
        el piso da 85% y el real anda cerca de 97%. El hueco es de entre 8 y 12
        puntos y NO se estima con un factor: se declara que falta.

        Sirve para dos cosas y nada más:
          · si el piso ya pasa de 100, la suscripción pierde plata con certeza;
          · ordena aseguradoras entre sí, porque el hueco es parecido.
        """
        p, s, a, _ = self._familia_seguro()
        if p is None or s is None or a is None:
            return None
        return round((s + abs(a)) / p * 100.0, 2)

    def base_seguro(self) -> Optional[str]:
        return self._familia_seguro()[3]

    def resumen(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "cik": self.cik,
            "nombre": self.nombre,
            "taxonomia": self.taxonomia,
            "moneda": self.moneda,
            "a_fecha": self.a_fecha,
            "periodo": self.periodo,
            "periodo_previo": self.periodo_previo,
            "origen": self.origen,
            "rancio": self.rancio,
            "edad_dias": self.edad_dias,
            "aviso": self.aviso,
            "declaracion_mas_nueva": self.declaracion_mas_nueva,
            "ausentes": self.ausentes,
            "publicado_mas_reciente": max(
                (h.publicado for h in self.campos.values() if h.publicado), default=None),
            "derivadas": {
                "dilucion_pct": self.dilucion_pct(),
                "fcf": self.fcf(),
                "ebitda": self.ebitda(),
                "deuda_neta": self.deuda_neta(),
                "deuda_ebitda": self.deuda_ebitda(),
                "ffo": self.ffo(),
                "payout_utilidad_pct": self.payout_utilidad_pct(),
                "payout_ffo_pct": self.payout_ffo_pct(),
                "cambio_patrimonio_pct": self.cambio_patrimonio_pct(),
                "split_probable": self.split_probable(),
                "siniestralidad_pct": self.siniestralidad_pct(),
                "ratio_combinado_piso_pct": self.ratio_combinado_piso_pct(),
            },
        }


class SinDatos(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Cliente
# ─────────────────────────────────────────────────────────────────────────────

class SEC:
    def __init__(self, cache_dir: str = CACHE_DIR, user_agent: str = USER_AGENT,
                 red: bool = True):
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.red = red                    # False = modo offline forzado (tests)
        os.makedirs(self.cache_dir, exist_ok=True)
        self._mapa: Optional[Dict[str, str]] = None
        self._ultimo = 0.0
        self.bitacora: List[str] = []

    # ── plomería ──
    def _log(self, msg: str) -> None:
        self.bitacora.append(msg)

    def _pausa(self) -> None:
        d = time.time() - self._ultimo
        if d < _PAUSA:
            time.sleep(_PAUSA - d)
        self._ultimo = time.time()

    def _get(self, url: str, host: str) -> Tuple[int, Optional[Any], str]:
        """Devuelve (status, json, motivo). status 0 = ni siquiera hubo conexión.

        Distinguir 'no hay red' de 'la SEC respondió 500' importa: en el primer
        caso degradamos a caché, en el segundo también, pero el aviso al usuario
        es distinto.
        """
        if not self.red:
            return 0, None, "modo sin red"
        self._pausa()
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": host,
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                crudo = r.read()
                if crudo[:2] == b"\x1f\x8b":
                    crudo = gzip.decompress(crudo)
                return r.status, json.loads(crudo.decode("utf-8")), ""
        except urllib.error.HTTPError as e:
            return e.code, None, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return 0, None, f"sin conexión: {e.reason}"
        except Exception as e:
            return 0, None, f"{type(e).__name__}: {e}"

    @staticmethod
    def _escribir_atomico(ruta: str, escribir) -> None:
        """Nunca dejar un .gz a medio escribir: una caché corrupta falla para
        siempre y en silencio."""
        tmp = ruta + ".tmp"
        try:
            escribir(tmp)
            os.replace(tmp, ruta)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # ── ticker -> CIK ──
    def mapa_tickers(self) -> Dict[str, str]:
        if self._mapa is not None:
            return self._mapa
        ruta = os.path.join(self.cache_dir, "company_tickers.json")
        if os.path.exists(ruta):
            edad = (time.time() - os.path.getmtime(ruta)) / 86400.0
            if edad < 30:
                try:
                    with open(ruta, encoding="utf-8") as f:
                        self._mapa = json.load(f)
                    return self._mapa
                except Exception:
                    pass
        st, data, motivo = self._get(
            "https://www.sec.gov/files/company_tickers.json", "www.sec.gov")
        if st == 200 and data:
            m = {str(v["ticker"]).upper(): str(v["cik_str"]).zfill(10)
                 for v in data.values()}
            self._escribir_atomico(ruta, lambda p: json.dump(
                m, open(p, "w", encoding="utf-8")))
            self._mapa = m
            return m
        # Sin red: la caché vieja sirve mejor que nada. Los CIK no cambian.
        if os.path.exists(ruta):
            try:
                with open(ruta, encoding="utf-8") as f:
                    self._mapa = json.load(f)
                self._log(f"mapa de tickers desde caché vieja ({motivo})")
                return self._mapa
            except Exception:
                pass
        self._mapa = {}
        return self._mapa

    def cik(self, ticker: str) -> Optional[str]:
        """Exacto primero. NUNCA recortar sufijos a ciegas: 'BRK-B' recortado a
        'BRK' devuelve otra empresa, y eso es peor que no encontrar nada."""
        m = self.mapa_tickers()
        t = ticker.upper().strip()
        if t in m:
            return m[t]
        # La SEC usa guion donde otros usan punto (BRK.B -> BRK-B).
        alt = t.replace(".", "-")
        if alt in m:
            return m[alt]
        return None

    # ── companyfacts, con degradación real ──
    def hechos(self, cik: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        cik10 = str(cik).zfill(10)
        f_datos = os.path.join(self.cache_dir, f"CIK{cik10}_facts.json.gz")
        f_meta = os.path.join(self.cache_dir, f"CIK{cik10}_meta.json")
        meta: Dict[str, Any] = {"origen": None, "edad_dias": None, "aviso": None}

        hay_cache = os.path.exists(f_datos)
        edad = ((time.time() - os.path.getmtime(f_datos)) / 86400.0) if hay_cache else None
        meta["edad_dias"] = round(edad, 1) if edad is not None else None

        def leer_cache() -> Optional[Dict[str, Any]]:
            try:
                with gzip.open(f_datos, "rt", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self._log(f"caché ilegible para CIK{cik10}: {e}")
                return None

        # 1. Caché fresca: ni preguntamos. Nadie reporta dos veces en una semana.
        if hay_cache and edad is not None and edad < FRESCURA_DIAS:
            d = leer_cache()
            if d:
                meta["origen"] = "cache"
                return d, meta

        # 2. Caché vieja: ¿hay declaración nueva? Si la red no responde,
        #    servimos la caché en vez de morir.
        ultima_prev = None
        if os.path.exists(f_meta):
            try:
                with open(f_meta, encoding="utf-8") as mf:
                    ultima_prev = json.load(mf).get("ultima_declaracion")
            except Exception:
                pass

        ultima_ahora, motivo_sub = self._ultima_declaracion(cik10)
        if hay_cache and ultima_ahora and ultima_ahora == ultima_prev:
            d = leer_cache()
            if d:
                meta["origen"] = "cache"
                os.utime(f_datos, None)   # se revalidó: reinicia el reloj
                return d, meta

        # 3. Descargar.
        st, data, motivo = self._get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json",
            "data.sec.gov")
        if st == 200 and data:
            self._escribir_atomico(f_datos, lambda p: _volcar_gz(p, data))
            self._escribir_atomico(f_meta, lambda p: json.dump(
                {"ultima_declaracion": ultima_ahora or ultima_prev,
                 "guardado": datetime.now(timezone.utc).isoformat()},
                open(p, "w", encoding="utf-8")))
            meta["origen"] = "red"
            meta["edad_dias"] = 0.0
            return data, meta

        # 4. Falló la descarga. ESTE es el caso que antes mataba la corrida
        #    del sábado: un 503 de la SEC y el sistema entero se quedaba sin
        #    fundamentales. Ahora servimos lo que tenemos y lo declaramos.
        if hay_cache:
            d = leer_cache()
            if d:
                meta["origen"] = "cache_rancio"
                meta["aviso"] = (f"la SEC no respondió ({motivo or motivo_sub}); "
                                 f"datos de hace {meta['edad_dias']} días")
                self._log(f"CIK{cik10}: sirviendo caché rancia — {meta['aviso']}")
                return d, meta

        meta["aviso"] = f"sin caché y sin red ({motivo or motivo_sub})"
        return None, meta

    def _ultima_declaracion(self, cik10: str) -> Tuple[Optional[str], str]:
        st, data, motivo = self._get(
            f"https://data.sec.gov/submissions/CIK{cik10}.json", "data.sec.gov")
        if st != 200 or not data:
            return None, motivo
        rec = (data.get("filings") or {}).get("recent") or {}
        for form, fecha in zip(rec.get("form", []), rec.get("filingDate", [])):
            if form in FORMULARIOS_ANUALES or form.startswith("10-Q"):
                return fecha, ""
        return None, "sin declaraciones periódicas"


def _volcar_gz(ruta: str, data: Any) -> None:
    with gzip.open(ruta, "wt", encoding="utf-8") as f:
        json.dump(data, f)


# ─────────────────────────────────────────────────────────────────────────────
# Extracción
# ─────────────────────────────────────────────────────────────────────────────

def _unidades_ordenadas(unidades: Dict[str, List[dict]], campo: str) -> List[str]:
    """Todas las unidades, la preferida primero.

    Antes se elegía UNA unidad y si esa no tenía hechos en el período, el campo
    quedaba ausente aunque otra unidad sí los tuviera. Así perdíamos entero el
    Banco de Chile: reporta en CLP y el módulo se quedaba mirando otra unidad.
    """
    if not unidades:
        return []
    pref = _unidad_preferida(unidades, campo)
    resto = [u for u in unidades if u != pref]
    return ([pref] if pref else []) + sorted(resto)


def _unidad_preferida(unidades: Dict[str, List[dict]], campo: str) -> Optional[str]:
    """Elegir la unidad a mano, nunca 'la primera del diccionario'.

    Un ADR chileno puede reportar el mismo concepto en CLP y en USD. Tomar la
    primera clave del dict y compararla contra un precio en dólares es
    exactamente el bug de moneda mezclada que estamos arreglando.
    """
    if not unidades:
        return None
    if campo in CAMPOS_ACCIONES:
        for u in ("shares", "pure"):
            if u in unidades:
                return u
        return next(iter(unidades))
    if "USD" in unidades:
        return "USD"
    monetarias = [u for u in unidades if len(u) == 3 and u.isalpha() and u.isupper()]
    if len(monetarias) == 1:
        return monetarias[0]
    if monetarias:
        # Varias monedas y ninguna USD: no adivinamos.
        return sorted(monetarias)[0]
    return next(iter(unidades))


ANCLAS = {
    "us-gaap": ["StockholdersEquity", "Assets", "NetIncomeLoss"],
    "ifrs-full": ["Equity", "Assets", "ProfitLoss"],
}
TOLERANCIA_DIAS = 6      # cierres fiscales que difieren por días entre etiquetas
DIAS_ANUAL = (300, 400)  # un hecho de flujo "anual" tiene que durar ~un año


def _visible(e: dict, a_fecha: Optional[str]) -> bool:
    """Point-in-time: solo lo publicado hasta a_fecha. Un hecho sin fecha de
    publicación no se puede afirmar como conocido, así que se descarta."""
    pub = e.get("filed")
    if not a_fecha:
        return True
    return bool(pub) and pub <= a_fecha


def _es_anual(e: dict) -> bool:
    """Un 10-K contiene también hechos trimestrales. Un flujo anual dura
    ~365 días; sin este filtro el dividendo de un trimestre se puede colar
    como si fuera el del año."""
    if e.get("form") not in FORMULARIOS_ANUALES:
        return False
    if not e.get("end"):
        return False
    ini = e.get("start")
    if not ini:
        return True          # hecho de balance (instantáneo)
    d = _dias(ini, e["end"])
    return d is not None and DIAS_ANUAL[0] <= d <= DIAS_ANUAL[1]


def _dias(a: str, b: str) -> Optional[int]:
    try:
        return abs((datetime.strptime(b[:10], "%Y-%m-%d")
                    - datetime.strptime(a[:10], "%Y-%m-%d")).days)
    except ValueError:
        return None


def _ancla(ns: Dict[str, Any], ns_nombre: str, a_fecha: Optional[str]) -> Optional[str]:
    """La fecha de cierre del último ejercicio visible.

    Existe porque sin ella cada campo se toma "el más reciente que haya" y se
    terminan mezclando períodos: deuda de 2026 contra EBITDA de 2025, o —caso
    real de NNN— un pagaré emitido el 15 de enero como si fuera el saldo del
    balance al 31 de diciembre.
    """
    fechas = []
    for concepto in ANCLAS.get(ns_nombre, []):
        nodo = ns.get(concepto)
        if not nodo:
            continue
        for entradas in (nodo.get("units") or {}).values():
            for e in entradas:
                if _visible(e, a_fecha) and _es_anual(e):
                    fechas.append(e["end"])
    return max(fechas) if fechas else None


def _en_periodo(entradas: List[dict], objetivo: str,
                a_fecha: Optional[str]) -> Optional[dict]:
    """El hecho de ese período exacto (±unos días). Si hay varios por
    reexpresión, gana el publicado más tarde de los visibles."""
    cands = []
    for e in entradas:
        if not (_visible(e, a_fecha) and _es_anual(e)):
            continue
        d = _dias(e["end"], objetivo)
        if d is not None and d <= TOLERANCIA_DIAS:   # 0 es válido, no falsy
            cands.append(e)
    if not cands:
        return None
    return sorted(cands, key=lambda x: (x.get("filed") or "", x.get("end") or ""))[-1]


def _a_hecho(e: dict, concepto: str, unidad: str) -> Optional[Hecho]:
    v = e.get("val")
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return Hecho(valor=v, concepto=concepto, unidad=unidad,
                 formulario=e.get("form") or "?", publicado=e.get("filed") or "",
                 periodo_fin=e.get("end") or "", anio_fiscal=e.get("fy"))


def fundamentales(ticker: str, a_fecha: Optional[str] = None,
                  cliente: Optional[SEC] = None) -> Optional[Fundamentales]:
    """Fundamentales de un ticker como se conocían en `a_fecha`.

    Devuelve None solo si no hay forma de saber nada: ticker inexistente, o sin
    caché y sin red. Un ticker que existe pero no reporta a la SEC devuelve un
    objeto con todos los campos ausentes y el motivo — no un error.
    """
    c = cliente or SEC()
    cik = c.cik(ticker)
    if not cik:
        return None

    data, meta = c.hechos(cik)
    if not data:
        return None

    facts = data.get("facts") or {}
    us = facts.get("us-gaap") or {}
    ifrs = facts.get("ifrs-full") or {}
    # Emisores extranjeros (20-F) usan ifrs-full. Algunos traen ambos: gana el
    # que tenga más conceptos, que es el que realmente usan.
    usa_ifrs = len(ifrs) > len(us)
    ns = ifrs if usa_ifrs else us
    ns_nombre = "ifrs-full" if usa_ifrs else "us-gaap"

    f = Fundamentales(
        ticker=ticker.upper(),
        cik=cik,
        nombre=data.get("entityName") or ticker.upper(),
        taxonomia=ns_nombre,
        a_fecha=a_fecha or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        origen=meta.get("origen") or "red",
        edad_dias=meta.get("edad_dias"),
        aviso=meta.get("aviso"),
    )

    ancla = _ancla(ns, ns_nombre, a_fecha)
    if not ancla:
        f.ausentes = sorted(CONCEPTOS)
        f.aviso = (f.aviso + " · " if f.aviso else "") + "sin ejercicio anual visible a la fecha"
        return f
    f.periodo = ancla

    # El ejercicio anterior: el cierre anual visible más cercano a un año antes.
    previos_posibles = []
    for concepto in ANCLAS.get(ns_nombre, []):
        nodo = ns.get(concepto)
        if not nodo:
            continue
        for entradas in (nodo.get("units") or {}).values():
            for e in entradas:
                if _visible(e, a_fecha) and _es_anual(e) and e["end"] < ancla:
                    d = _dias(e["end"], ancla)
                    if d and 300 <= d <= 430:
                        previos_posibles.append(e["end"])
    ancla_prev = max(previos_posibles) if previos_posibles else None
    f.periodo_previo = ancla_prev

    for campo, cascada in CONCEPTOS.items():
        elegido = None
        for concepto in cascada.get(ns_nombre, []):
            nodo = ns.get(concepto)
            if not nodo:
                continue
            unidades = nodo.get("units") or {}
            for u in _unidades_ordenadas(unidades, campo):
                e = _en_periodo(unidades.get(u) or [], ancla, a_fecha)
                if e:
                    elegido = (concepto, u, unidades.get(u) or [])
                    break
            if elegido:
                break
        if not elegido:
            f.ausentes.append(campo)
            continue

        concepto, u, entradas = elegido
        h = _a_hecho(_en_periodo(entradas, ancla, a_fecha), concepto, u)
        if not h:
            f.ausentes.append(campo)
            continue
        f.campos[campo] = h

        # El comparable del año anterior sale del MISMO concepto y del mismo
        # cierre anual anterior. Nunca de "la penúltima entrada que haya":
        # una reexpresión publica el mismo año otra vez y la dilución daría 0%.
        if ancla_prev:
            ep = _en_periodo(entradas, ancla_prev, a_fecha)
            if ep:
                hp = _a_hecho(ep, concepto, u)
                if hp:
                    f.previos[campo] = hp

    _acciones_de_portada(f, facts, a_fecha, ancla)
    _sumar_arriendos(f)
    _avisar_declaracion_mas_nueva(f, facts, a_fecha, ancla)
    return f


def _avisar_declaracion_mas_nueva(f: Fundamentales, facts: Dict[str, Any],
                                  a_fecha: Optional[str], ancla: str) -> None:
    """Detecta que hay una memoria anual más reciente sin datos utilizables.

    Caso real: el Banco de Chile presentó su 20-F de 2025 el 2026-04-30 —la
    portada está etiquetada— pero sus estados financieros no aparecen en los
    datos estructurados. Faro usaría los números de 2024 sin decir nada.

    Usar el dato viejo está bien; no avisarlo, no. Regla 9 del mandato.
    """
    nodo = (facts.get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    if not nodo:
        return
    posteriores = []
    for entradas in (nodo.get("units") or {}).values():
        for e in entradas:
            if not _visible(e, a_fecha):
                continue
            if e.get("form") not in FORMULARIOS_ANUALES or not e.get("end"):
                continue
            d = _dias(ancla, e["end"])
            if e["end"] > ancla and d is not None and d > 210:
                posteriores.append((e["end"], e.get("filed"), e.get("form")))
    if not posteriores:
        return
    fin, publicado, form = max(posteriores)
    f.declaracion_mas_nueva = {"periodo": fin, "publicado": publicado, "formulario": form}
    aviso = (f"hay un {form} que cubre hasta {fin} (publicado {publicado}) cuyos estados "
             f"no están en los datos estructurados; se usa el ejercicio {ancla}")
    f.aviso = (f.aviso + " · " if f.aviso else "") + aviso


def _acciones_de_portada(f: Fundamentales, facts: Dict[str, Any],
                         a_fecha: Optional[str], ancla: Optional[str] = None) -> None:
    """Acciones en circulación desde la portada del 10-K/20-F (namespace dei).

    Es el único conteo que refleja las recompras: el papel recomprado sigue
    'emitido' pero deja de estar en circulación. Su fecha es la de la portada
    (unas semanas después del cierre), así que no se ancla al ejercicio; se
    emparejan dos portadas separadas por ~un año.
    """
    nodo = (facts.get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    if not nodo:
        return
    entradas = (nodo.get("units") or {}).get("shares") or []
    por_fecha: Dict[str, dict] = {}
    for e in entradas:
        if not _visible(e, a_fecha):
            continue
        if e.get("form") not in FORMULARIOS_ANUALES or not e.get("end"):
            continue
        k = e["end"]
        if k not in por_fecha or (e.get("filed") or "") > (por_fecha[k].get("filed") or ""):
            por_fecha[k] = e
    fechas = sorted(por_fecha)
    if len(fechas) < 2:
        return

    # La portada del 10-K se firma unas semanas DESPUÉS del cierre. Hay que
    # tomar la que corresponde a ESTE ejercicio, no simplemente la última
    # disponible: el Banco de Chile tenía datos financieros de 2024 y una
    # portada de 2025, y se estaban comparando cosas de años distintos.
    if ancla:
        candidatas = [k for k in fechas
                      if k >= ancla and (_dias(ancla, k) or 999) <= 210]
        if candidatas:
            fechas = [k for k in fechas if k <= candidatas[0]]
        else:
            previas = [k for k in fechas if k <= ancla]
            if previas:
                fechas = previas
    if len(fechas) < 2:
        return
    act = por_fecha[fechas[-1]]
    prev = None
    for k in reversed(fechas[:-1]):
        d = _dias(k, fechas[-1])
        if d is not None and 300 <= d <= 430:
            prev = por_fecha[k]
            break
    if not prev:
        return
    ha = _a_hecho(act, "dei:EntityCommonStockSharesOutstanding", "shares")
    hp = _a_hecho(prev, "dei:EntityCommonStockSharesOutstanding", "shares")
    if ha and hp and ha.valor > 0 and hp.valor > 0:
        f.campos["acciones_circulacion"] = ha
        f.previos["acciones_circulacion"] = hp
        if "acciones_circulacion" in f.ausentes:
            f.ausentes.remove("acciones_circulacion")


# Qué componentes hay que sumarle a cada concepto "base" de deuda para llegar
# al total, sin contar dos veces. Depende de qué incluye ya el concepto base.
COMPONENTES_DEUDA = {
    # el combinado ya trae corto y largo plazo: solo faltan arriendos
    "DebtLongtermAndShorttermCombinedAmount":
        ("arriendo_no_corriente", "arriendo_corriente",
         "arriendo_financiero_no_corriente", "arriendo_financiero_corriente"),
    # este ya incluye arriendos financieros
    "LongTermDebtAndCapitalLeaseObligations":
        ("deuda_corto_plazo", "arriendo_no_corriente", "arriendo_corriente"),
    # 'LongTermDebt' suele traer la porción corriente adentro
    "LongTermDebt":
        ("deuda_corto_plazo", "arriendo_no_corriente", "arriendo_corriente",
         "arriendo_financiero_no_corriente", "arriendo_financiero_corriente"),
    "NotesPayable":
        ("deuda_corto_plazo", "arriendo_no_corriente", "arriendo_corriente",
         "arriendo_financiero_no_corriente", "arriendo_financiero_corriente"),
    # este es SOLO el largo plazo: hay que sumarle la porción corriente
    "LongTermDebtNoncurrent":
        ("deuda_corriente", "deuda_corto_plazo",
         "arriendo_no_corriente", "arriendo_corriente",
         "arriendo_financiero_no_corriente", "arriendo_financiero_corriente"),
    "Borrowings":
        ("arriendo_no_corriente", "arriendo_corriente"),
    "NoncurrentBorrowings":
        ("deuda_corriente", "arriendo_no_corriente", "arriendo_corriente"),
}
PARTES_POR_DEFECTO = ("arriendo_no_corriente", "arriendo_corriente")


def _sumar_arriendos(f: Fundamentales) -> None:
    """Deuda total = deuda financiera + porción corriente + arriendos.

    Dos motivos, los dos verificados sobre empresas reales:

    · Desde ASC 842 / NIIF 16 el arriendo operativo es un pasivo del balance y
      las clasificadoras lo cuentan como deuda. Sin él, Walmart pasa de 1,2x a
      0,6x.
    · El concepto base a veces es solo el largo plazo. MGE Energy etiqueta
      792 M en 'LongTermDebtNoncurrent' y aparte 21,6 M corrientes, 94,5 M de
      corto plazo y 19,6 M de arriendo financiero: el total real es ~936 M, no
      792 M.

    Qué se suma depende de qué trae el concepto base, para no contar dos veces.
    """
    base = f.campos.get("deuda_total")
    if base is None:
        return
    extra = 0.0
    partes = []
    for k in COMPONENTES_DEUDA.get(base.concepto, PARTES_POR_DEFECTO):
        h = f.campos.get(k)
        if h and h.unidad == base.unidad:
            extra += h.valor
            partes.append(h.concepto)
    if not partes:
        return
    f.campos["deuda_total"] = Hecho(
        valor=base.valor + extra,
        concepto=base.concepto + " + " + " + ".join(partes),
        unidad=base.unidad,
        formulario=base.formulario,
        publicado=base.publicado,
        periodo_fin=base.periodo_fin,
        anio_fiscal=base.anio_fiscal,
    )
    f.campos["deuda_financiera"] = base


# ─────────────────────────────────────────────────────────────────────────────
# Puente con el motor de reglas
# ─────────────────────────────────────────────────────────────────────────────

# Campos del dict que consume rules.evaluar() y que la SEC resuelve mejor que
# Yahoo. Cada uno con cómo se obtiene.
def _derivadas_para_reglas(f: "Fundamentales") -> Dict[str, Optional[float]]:
    ni = f.val("utilidad_neta")
    pat = f.val("patrimonio")
    return {
        "share_change_pct": f.dilucion_pct(),
        "fcf": f.fcf(),
        "cash": f.val("caja"),
        "net_debt_ebitda": f.deuda_ebitda(),
        "payout": f.payout_utilidad_pct(),
        "payout_ffo": f.payout_ffo_pct(),
        "roe": (ni / pat * 100.0) if (ni is not None and pat and pat > 0) else None,
        "dilucion_dudosa": f.split_probable() or None,
        "siniestralidad": f.siniestralidad_pct(),
        "ratio_combinado_piso": f.ratio_combinado_piso_pct(),
    }

# Campos donde Yahoo es directamente poco fiable y preferimos la ausencia
# declarada antes que su número. El payout sobre FFO es el caso extremo:
# Yahoo no lo calcula, calcula el contable y lo llama payout.
SOLO_SEC = {"payout_ffo", "share_change_pct"}


def enriquecer(d: Dict[str, Any], ticker: Optional[str] = None,
               cliente: Optional[SEC] = None,
               a_fecha: Optional[str] = None) -> Dict[str, Any]:
    """Sobrescribe en `d` los fundamentales con los de la SEC.

    Yahoo queda para lo que hace bien: precio, volumen, capitalización y
    dividendos. Los fundamentales vienen de documentos con fecha.

    Deja rastro de todo:
      d["fuentes"][campo]  -> "sec" | "yahoo" | "ausente"
      d["sec"]             -> resumen de procedencia (rancio, aviso, publicado)

    No inventa nada y no borra nada útil: si la SEC no tiene un campo y Yahoo
    sí, se conserva el de Yahoo marcado como tal — salvo en SOLO_SEC, donde la
    ausencia es preferible al número malo.
    """
    tk = (ticker or d.get("ticker") or "").upper()
    fuentes: Dict[str, str] = dict(d.get("fuentes") or {})

    if not tk or d.get("es_fondo"):
        # Un ETF no declara a la SEC como empresa: no es un fallo, no aplica.
        d["fuentes"] = fuentes
        return d

    f = fundamentales(tk, a_fecha=a_fecha, cliente=cliente)
    if f is None:
        d["sec"] = {"estado": "sin_datos",
                    "motivo": "no declara a la SEC o no se pudo resolver el CIK"}
        for k in SOLO_SEC:
            if d.get(k) is not None:
                d[k] = None
                fuentes[k] = "ausente"
        d["fuentes"] = fuentes
        return d

    # Guardia de moneda: si reporta en algo distinto de USD, cruzar esos
    # fundamentales con un precio en dólares produce ratios sin sentido.
    moneda = f.moneda
    mezcla = bool(moneda and moneda != "USD")

    for campo, valor in _derivadas_para_reglas(f).items():
        if valor is not None:
            d[campo] = valor
            fuentes[campo] = "sec"
        elif campo in SOLO_SEC:
            d[campo] = None
            fuentes[campo] = "ausente"
        elif d.get(campo) is not None:
            fuentes[campo] = "yahoo"
        else:
            fuentes[campo] = "ausente"

    if f.base_seguro():
        d["base_seguro"] = f.base_seguro()
    if f.payout_ffo_pct() is not None:
        d["ffo_origen"] = "SEC XBRL · utilidad neta + D&A del 10-K/20-F"

    d["fuentes"] = fuentes
    d["sec"] = {
        "estado": "ok",
        "cik": f.cik,
        "nombre": f.nombre,
        "taxonomia": f.taxonomia,
        "moneda": moneda,
        "moneda_distinta_usd": mezcla,
        "rancio": f.rancio,
        "edad_dias": f.edad_dias,
        "aviso": f.aviso,
        "publicado": max((h.publicado for h in f.campos.values() if h.publicado), default=None),
        "periodo": max((h.periodo_fin for h in f.campos.values() if h.periodo_fin), default=None),
        "formulario": (f.campos.get("utilidad_neta").formulario
                       if f.campos.get("utilidad_neta") else None),
        "ausentes": f.ausentes,
    }
    return d
