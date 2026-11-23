# Fragen an die Steuerberatung: Umsatzsteuer-Voranmeldung, ZM und Buchhaltung in permitA38

**Worum es geht.** permitA38 ist eine Bürosoftware für Freiberufler und kleine Betriebe (Angebote, Rechnungen,
Zeiterfassung, Ausgaben). Die nächste Version (0.5.0-alpha.1) bekommt eine Buchhaltung: ein unveränderliches Journal
(SKR03 oder SKR04, EÜR oder Bilanzierung, Ist- oder Soll-Versteuerung), daraus abgeleitet die
Umsatzsteuer-Voranmeldung, die Zusammenfassende Meldung und ein DATEV-Export für Sie. Die Software übermittelt nichts
an das Finanzamt; der Nutzer überträgt die Zahlen selbst in Mein ELSTER bzw. das BZSt-Portal.

Alle Regeln unten haben wir nach bestem Wissen umgesetzt, aber **nicht von einer Steuerberatung bestätigt**. Bis Sie
sie bestätigt haben, zeigt die Software den Hinweis „Nicht von einer Steuerberatung bestätigt“. Bitte antworten Sie je
Frage kurz mit **richtig**, **falsch, richtig ist: …** oder **kommt darauf an: …**. Die Nummern (TA-27 …) entsprechen
unserer internen Liste. Alle Beträge sind erfundene Beispielzahlen.

---

## A. Umsatzsteuer-Voranmeldung

**TA-27 Kennzahlen 2026.** Wir verwenden: 81, 86 (steuerpflichtige Umsätze 19 % / 7 %, Steuer rechnet das Formular),
35/36 (andere Steuersätze), 21 (nicht steuerbare sonstige Leistungen an Unternehmer in der EU, § 18b), 46/47 (§ 13b
Abs. 1, Leistungen von Unternehmern aus dem übrigen Gemeinschaftsgebiet), 84/85 (andere § 13b-Leistungen, darunter
Leistungen von Unternehmern aus dem Drittland nach § 13b Abs. 2 Nr. 1), 66, 67, 39, 83 und 10 (berichtigte
Anmeldung). Rechnung: **Kz 83 = Steuer 81 + Steuer 86 + 36 + 47 + 85 − 66 − 67 − 39.**
Stimmen Nummern, Bedeutung und Rechenweg für 2026?
Antwort:

**TA-28 Rundung.** Bemessungsgrundlagen werden auf volle Euro **abgeschnitten**, auch bei negativen Summen (−300,50 →
−300). Bei 81/86 rechnet das Formular die Steuer aus der abgerundeten Basis; 36, 47, 85 tragen wir mit der gebuchten
Steuer auf den Cent ein. Beispiel: Rechnungen netto 1.000,00 und 2.500,50 (USt 190,00 + 475,10): Kz 81 = 3.500, Steuer
laut Formular 665,00, gebucht 665,10. Die Differenz von 0,10 € bleibt auf den USt-Konten bis zur Jahreserklärung.
Richtig so? Auch bei negativen Summen?
Antwort:

**TA-29 Leistungszeitpunkt (Soll).** Als Zeitpunkt der Leistung nehmen wir das Ende des Leistungszeitraums auf der
Rechnung, sonst den Beginn, sonst das Rechnungsdatum. Ausreichend?
Antwort:

**TA-30 Teilzahlungen (Ist).** Bei Teilzahlungen versteuern wir anteilig, kumuliert über die Steuersätze. Beispiel:
Rechnung 19 % netto 1.000,00 (USt 190,00) und 7 % netto 500,00 (USt 35,00), brutto 1.725,00; Zahlung 1.000,00 im März:
März Kz 81 Basis 579,71 / Steuer 110,15 und Kz 86 Basis 289,85 / Steuer 20,29; Restzahlung 725,00 im April:
Kz 81 420,29 / 79,85, Kz 86 210,15 / 14,71. Zusammen genau die Rechnung. Ist diese Aufteilung vertretbar?
Antwort:

**TA-31 Gutschriften / Stornorechnungen.** Eine Minderung (Rechnungskorrektur, Storno, Skonto) wirkt im Zeitraum der
Korrektur, nie im ursprünglichen Zeitraum, und zwar
– für den noch **nicht bezahlten** Teil sofort mit dem Datum der Korrektur,
– für den **bereits bezahlten** Teil erst bei Rückzahlung (Soll und Ist).
Beispiel (Soll): Rechnung Februar 1.190,00 bezahlt, Storno am 2. März, Rückzahlung am 15. April → keine Änderung im
März, im April Kz 81 −1.000 / −190,00.
Eine Zahlung nach einer Forderungsabschreibung macht die Steuer wieder fällig (Zahlungsdatum). Eine Überzahlung über
den Rechnungsbetrag hinaus behandeln wir **ohne** Umsatzsteuer (Guthaben des Kunden).
Richtig? Wann ist stattdessen eine berichtigte Voranmeldung nötig? Ist eine Überzahlung steuerpflichtiges Entgelt?
Antwort:

**TA-32 Forderungsabschreibung.** Soll: Minderung zum Datum der Abschreibung (§ 17 Abs. 2). Ist: keine Änderung für
den unbezahlten Rest. Richtig? Welche Dokumentation brauchen Sie?
Antwort:

**TA-33 Vorsteuer-Zeitpunkt.** Vorsteuer ziehen wir im Zeitraum des **Rechnungsdatums** ab (als Ersatz für den
Rechnungseingang), bei Soll und Ist gleich. Wird eine Rechnung erst nach Abgabe der Voranmeldung erfasst, ziehen wir
sie im nächsten offenen Monat ab. Ist das in Ordnung? Kommt 2028 die Änderung, dass Vorsteuer aus Rechnungen von
Ist-Versteuerern erst bei Zahlung abziehbar ist?
Antwort:

**TA-34 § 13b Zeitpunkte.** EU-Leistung (Abs. 1): Zeitraum der Leistung. Drittland (Abs. 2 Nr. 1): Rechnungsdatum,
spätestens Ende des Folgemonats der Leistung. Beispiel: Cloud-Software aus der EU, netto 200,50, Leistung 20. Mai,
Rechnung 2. Juni → Mai: Kz 46 = 200, Kz 47 = 38,10, Kz 67 = 38,10. Gleiche Leistung aus den USA, Rechnung 10. Juli →
Juni (Kz 84/85). Richtig?
Antwort:

**TA-35 § 13b, nicht abziehbarer Teil.** Ist der Vorsteuerabzug nur teilweise möglich (z. B. 50 %), buchen wir den
nicht abziehbaren Teil der selbst geschuldeten Steuer als Kosten (§ 9b EStG). Richtig?
Antwort:

**TA-36 Kleinunternehmer mit § 13b-Bezügen.** Ein Kleinunternehmer, der z. B. Software aus der EU bezieht, gibt für das
**Kalendervierteljahr** eine Voranmeldung ab (Kz 46/47 bzw. 84/85, kein Vorsteuerabzug). Quartal oder Monat?
Welche Vorschrift gilt nach der Reform 2025?
Antwort:

**TA-37 Kleinunternehmergrenzen.** 25.000 € Vorjahr / 100.000 € laufendes Jahr, netto, gemessen an den vereinnahmten
Umsätzen. Wie wird das Jahr des Überschreitens behandelt? Vereinnahmt oder vereinbart?
Antwort:

**TA-38 Voranmeldungszeitraum.** Hinweis aus der Vorjahressteuer (über 9.000 € monatlich, bis 2.000 € Befreiung
möglich), § 18 Abs. 2a. Gilt für Neugründungen ab 2027 wieder die monatliche Abgabe?
Antwort:

**TA-39 Dauerfristverlängerung / Sondervorauszahlung.** 1/11 von (Summe Kz 83 des Vorjahres + Kz 39 des Vorjahres),
bei Gründung im Vorjahr hochgerechnet, nie negativ; Anrechnung als Kz 39 im Dezember; Quartalszahler zahlen keine.
Beispiel: Vorjahr Kz 83 = 6.000,00, Kz 39 = 600,00 → Sondervorauszahlung 600,00. Rundung? Wie zählen Erstattungen
und berichtigte Anmeldungen? Kz 39 in vollen Euro?
Antwort:

**TA-40 Fristen.** 10. Tag nach Ablauf des Zeitraums, Verschiebung auf den nächsten Werktag nach § 108 Abs. 3 AO mit
den Feiertagen des Bundeslandes des Betriebs; die drei Tage Zahlungsschonfrist zeigen wir nur als Hinweis an. Richtig?
Antwort:

## B. Zusammenfassende Meldung

**TA-41 ZM.** Nur sonstige Leistungen, immer vierteljährlich, Abgabe bis zum 25. Tag nach Quartalsende. Gutschriften
und Skonti melden wir (negativ) im Quartal der Änderung; nur eine bei Abgabe falsche Meldung wird im ursprünglichen
Quartal berichtigt. Beträge in vollen Euro. Kleinunternehmer geben keine ZM ab. Beispiel: Leistung an Kunde A im
März 2.000,70, Gutschrift 500,00 im Mai → ZM Q1: 2.000 (mit weiteren Leistungen), ZM Q2: −500. Richtig? Was gilt bei
einem Storno einer von Anfang an falschen Rechnung (falscher Kunde, falsche USt-IdNr.)?
Antwort:

## C. Buchhaltung und EÜR

**TA-42 Wechsel der Besteuerungsart.** Wechsel von EÜR/Bilanz oder Ist/Soll nur zum Jahresbeginn; bei offenen
Posten lehnen wir den Wechsel vorerst ab. Wie werden offene Posten und noch nicht fällige Umsatzsteuer übernommen?
Antwort:

**TA-43 EÜR-Darstellung.** Brutto-Methode: vereinnahmte USt als Einnahme, gezahlte Vorsteuer und an das Finanzamt
gezahlte USt als Ausgabe. Nicht abziehbarer Teil der § 13b-Steuer nur einmal (über die Zahlung an das Finanzamt).
Privatanteile nie in der EÜR. Bewirtung 30 % und Gewerbesteuer als nicht abziehbare Ausgaben gesondert. Der
Privatanteil der nicht abziehbaren § 13b-Steuer mindert die „gezahlte USt“ zum Buchungsdatum der Rechnung, das in einem
anderen Jahr liegen kann als die Zahlung an das Finanzamt. Stimmt die Zeilenzuordnung zur Anlage EÜR 2026?
Antwort:

**TA-44 Kontenrahmen und DATEV.** SKR03/SKR04-Konten für jede Funktion (Erlöse 19 %/7 %, USt, Vorsteuer, § 13b,
Forderungen, Verbindlichkeiten, Verrechnungskonten für die EÜR), Automatikkonten, BU-Schlüssel inkl. § 13b, BU 40 zum
Aufheben der Automatik, Sammelkonten statt Personenkonten, ein Buchungsstapel je Wirtschaftsjahr. Welche Konten und
Schlüssel erwarten Sie? **Bitte importieren Sie einen Test-Export in DATEV**, bevor wir die Version freigeben.
Antwort:

**TA-45 GoBD.** Das Journal ist unveränderlich (nur Gegenbuchungen, Hash-Kette). Mit dem Abschluss einer
Voranmeldung werden deren Monate festgeschrieben; Belege, die danach kommen, landen im nächsten offenen Monat. Genügt
das als Festschreibung? Reicht der gespeicherte Stand der Voranmeldung mit den zugehörigen Buchungen als
Dokumentation?
Antwort:

---

**Rückgabe.** Bitte senden Sie die ausgefüllte Liste an die Person, von der Sie sie bekommen haben. Wir tragen Ihre
Antworten ein, korrigieren die Regeln (sie liegen als Daten vor, nicht im Programmcode) und nehmen den Hinweis „nicht
bestätigt“ für jede bestätigte Regel zurück.
