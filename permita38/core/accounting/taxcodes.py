"""Tax codes carried by journal lines (the base of the VAT return, see docs/accounting.md).

A *base line* (the revenue or expense line of a posting) carries ``tax_code`` always. ``tax_base_cents`` /
``tax_amount_cents`` on it (or, for VAT paid on receipt, on the payment's VAT line) hold the amounts that are
*taxable at the date of that entry*; they are signed (a credit note or a reversal carries negative values), so
summing them over a period gives the correct figure including all corrections.
"""

# output (sales) codes
UST19 = "UST19"  # taxable sales at 19 %
UST7 = "UST7"  # taxable sales at 7 %
UST0 = "UST0"  # sales without VAT (tax-exempt / not taxable / reverse charge: refine in the VAT return)
KLEIN = "KLEIN"  # sales by a Kleinunternehmer (§ 19 UStG), no VAT
# input (purchase) codes
VST19 = "VST19"  # purchases with 19 % input VAT
VST7 = "VST7"  # purchases with 7 % input VAT
VST0 = "VST0"  # purchases without input VAT

OUTPUT_CODES = (UST19, UST7, UST0, KLEIN)
INPUT_CODES = (VST19, VST7, VST0)
ALL_CODES = OUTPUT_CODES + INPUT_CODES

CODE_RATE = {UST19: 19, UST7: 7, UST0: 0, KLEIN: 0, VST19: 19, VST7: 7, VST0: 0}
CODE_DESCRIPTION = {
    UST19: "Sales at 19 % VAT",
    UST7: "Sales at 7 % VAT",
    UST0: "Sales without VAT (exempt, not taxable, reverse charge)",
    KLEIN: "Sales without VAT as a Kleinunternehmer (§ 19 UStG)",
    VST19: "Purchases with 19 % input VAT",
    VST7: "Purchases with 7 % input VAT",
    VST0: "Purchases without input VAT",
}
