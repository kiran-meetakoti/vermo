import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, euro } from "@/constants/vermo";
import { useAuth } from "@/lib/auth";
import { chartPoints, debtProjection, inferAnnualInterestRate } from "@/lib/debt-math";
import { supabase } from "@/lib/supabase";

// Same budget_settings keys the web app's Debt tracker reads and writes.
const DEBT_KEYS = {
  originalPrincipal: "debt_original_principal_eur",
  balance: "debt_balance_eur",
  monthlyPayment: "debt_monthly_payment_eur",
  originalTerm: "debt_original_term_months",
  paidMonths: "debt_paid_months",
  annualInterest: "debt_annual_interest_pct",
} as const;

const EXTRA_MONTHLY_OPTIONS = [0, 100, 250, 500];

type DebtSettings = {
  originalPrincipal: number;
  balance: number;
  monthlyPayment: number;
  originalTerm: number;
  paidMonths: number;
  annualInterest: number;
};

const EMPTY: DebtSettings = {
  originalPrincipal: 0,
  balance: 0,
  monthlyPayment: 0,
  originalTerm: 0,
  paidMonths: 0,
  annualInterest: 0,
};

function compactEuro(value: number): string {
  return value >= 1000 ? `€${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k` : `€${Math.round(value)}`;
}

// Same thresholds as the web chart's done/low/mid bar classes.
function barColor(remaining: number, startBalance: number): string {
  if (remaining < startBalance * 0.2) return colors.positive;
  if (remaining < startBalance * 0.45) return colors.brandBright;
  if (remaining < startBalance * 0.7) return "#f59e0b";
  return colors.negative;
}

function payoffLabel(months: number, payoffDate: Date | null): string {
  if (!Number.isFinite(months) || !payoffDate) return "Not reducing";
  if (months === 0) return "Paid off";
  return payoffDate.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

export default function DebtTab() {
  const { session } = useAuth();
  const [settings, setSettings] = useState<DebtSettings>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [extraMonthly, setExtraMonthly] = useState(0);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<Record<keyof DebtSettings, string>>({
    originalPrincipal: "",
    balance: "",
    monthlyPayment: "",
    originalTerm: "",
    paidMonths: "",
    annualInterest: "",
  });
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const { data } = await supabase
      .from("budget_settings")
      .select("key, value")
      .in("key", Object.values(DEBT_KEYS));
    const byKey = new Map((data ?? []).map((row) => [row.key as string, Number(row.value)]));
    const next: DebtSettings = {
      originalPrincipal: byKey.get(DEBT_KEYS.originalPrincipal) ?? 0,
      balance: byKey.get(DEBT_KEYS.balance) ?? 0,
      monthlyPayment: byKey.get(DEBT_KEYS.monthlyPayment) ?? 0,
      originalTerm: Math.trunc(byKey.get(DEBT_KEYS.originalTerm) ?? 0),
      paidMonths: Math.trunc(byKey.get(DEBT_KEYS.paidMonths) ?? 0),
      annualInterest: byKey.get(DEBT_KEYS.annualInterest) ?? 0,
    };
    setSettings(next);
    setForm({
      originalPrincipal: String(next.originalPrincipal || ""),
      balance: String(next.balance || ""),
      monthlyPayment: String(next.monthlyPayment || ""),
      originalTerm: String(next.originalTerm || ""),
      paidMonths: String(next.paidMonths || ""),
      annualInterest: String(next.annualInterest || ""),
    });
    setLoaded(true);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const remainingMonths = Math.max(settings.originalTerm - settings.paidMonths, 0);
  const interestRate =
    settings.annualInterest > 0
      ? settings.annualInterest
      : inferAnnualInterestRate(settings.balance, settings.monthlyPayment, remainingMonths);
  const progress =
    settings.originalPrincipal > 0
      ? Math.min((1 - settings.balance / settings.originalPrincipal) * 100, 100)
      : 0;

  const projection = useMemo(
    () => debtProjection(settings.balance, settings.monthlyPayment, extraMonthly, 0, interestRate),
    [settings, extraMonthly, interestRate],
  );
  const baseline = useMemo(
    () => debtProjection(settings.balance, settings.monthlyPayment, 0, 0, interestRate),
    [settings, interestRate],
  );
  const monthsSaved =
    Number.isFinite(projection.months) && Number.isFinite(baseline.months)
      ? baseline.months - projection.months
      : 0;
  const interestSaved =
    Number.isFinite(projection.totalInterest) && Number.isFinite(baseline.totalInterest)
      ? baseline.totalInterest - projection.totalInterest
      : 0;
  const bars = chartPoints(projection.schedule);
  const maxBar = Math.max(settings.balance, ...bars.map((p) => p.remaining), 1);

  async function saveSettings() {
    const parsed: Record<string, number> = {};
    for (const [field, text] of Object.entries(form)) {
      const value = parseFloat((text || "0").replace(",", "."));
      if (!Number.isFinite(value) || value < 0) {
        setFormError("All fields must be non-negative numbers.");
        return;
      }
      parsed[field] = value;
    }
    if (parsed.paidMonths > parsed.originalTerm) {
      setFormError("Paid months can't exceed the original term.");
      return;
    }
    setSaving(true);
    setFormError(null);
    const userId = session!.user.id;
    const updatedAt = new Date().toISOString();
    // RLS WITH CHECK requires user_id = auth.uid() — set it explicitly.
    const { error } = await supabase.from("budget_settings").upsert(
      [
        { user_id: userId, key: DEBT_KEYS.originalPrincipal, value: parsed.originalPrincipal, updated_at: updatedAt },
        { user_id: userId, key: DEBT_KEYS.balance, value: parsed.balance, updated_at: updatedAt },
        { user_id: userId, key: DEBT_KEYS.monthlyPayment, value: parsed.monthlyPayment, updated_at: updatedAt },
        { user_id: userId, key: DEBT_KEYS.originalTerm, value: Math.trunc(parsed.originalTerm), updated_at: updatedAt },
        { user_id: userId, key: DEBT_KEYS.paidMonths, value: Math.trunc(parsed.paidMonths), updated_at: updatedAt },
        { user_id: userId, key: DEBT_KEYS.annualInterest, value: parsed.annualInterest, updated_at: updatedAt },
      ],
      { onConflict: "user_id,key" },
    );
    setSaving(false);
    if (error) {
      setFormError(error.message);
      return;
    }
    setShowForm(false);
    await load();
  }

  const hasDebt = settings.balance > 0;

  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />}
      >
        <View style={styles.headerRow}>
          <Text style={styles.pageTitle}>Debt</Text>
          <Pressable onPress={() => setShowForm((v) => !v)} hitSlop={10}>
            <Text style={styles.editToggle}>{showForm ? "Close" : hasDebt ? "Edit loan" : "+ Add loan"}</Text>
          </Pressable>
        </View>

        {showForm ? (
          <View style={styles.form}>
            {(
              [
                ["originalPrincipal", "Original loan (EUR)"],
                ["balance", "Remaining balance (EUR)"],
                ["monthlyPayment", "Monthly payment (EUR)"],
                ["originalTerm", "Original term (months)"],
                ["paidMonths", "Paid months"],
                ["annualInterest", "Annual interest % (0 = infer)"],
              ] as [keyof DebtSettings, string][]
            ).map(([field, label]) => (
              <View key={field}>
                <Text style={styles.fieldLabel}>{label}</Text>
                <TextInput
                  style={styles.input}
                  placeholder="0"
                  placeholderTextColor={colors.muted}
                  keyboardType="decimal-pad"
                  value={form[field]}
                  onChangeText={(text) => setForm((f) => ({ ...f, [field]: text }))}
                />
              </View>
            ))}
            {formError ? <Text style={styles.error}>{formError}</Text> : null}
            <Pressable style={styles.saveButton} onPress={saveSettings} disabled={saving}>
              <Text style={styles.saveButtonText}>{saving ? "Saving…" : "Save loan details"}</Text>
            </Pressable>
          </View>
        ) : null}

        {!loaded ? null : !hasDebt && !showForm ? (
          <Text style={styles.empty}>
            No loan tracked. Add one above to model your payoff — balance, payment, and term are enough.
          </Text>
        ) : (
          <>
            <View style={styles.tileRow}>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>BALANCE</Text>
                <Text style={[styles.tileValue, { color: colors.negative }]}>{euro(settings.balance)}</Text>
              </View>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>MONTHLY PAYMENT</Text>
                <Text style={styles.tileValue}>{euro(settings.monthlyPayment)}</Text>
              </View>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>MONTHS LEFT</Text>
                <Text style={styles.tileValue}>{remainingMonths}</Text>
              </View>
            </View>

            <View style={styles.card}>
              <Text style={styles.tileLabel}>PAID OFF</Text>
              <View style={styles.progressTrack}>
                <View style={[styles.progressFill, { width: `${Math.max(progress, 0)}%` }]} />
              </View>
              <Text style={styles.progressText}>
                {progress.toFixed(0)}% of {euro(settings.originalPrincipal)} · {interestRate.toFixed(1)}% annual rate
                {settings.annualInterest > 0 ? "" : " (inferred)"}
              </Text>
            </View>

            <View style={styles.card}>
              <Text style={styles.tileLabel}>PAYOFF PROJECTION</Text>
              <Text style={styles.payoffDate}>{payoffLabel(projection.months, projection.payoffDate)}</Text>
              <Text style={styles.payoffSub}>
                {Number.isFinite(projection.months)
                  ? `${projection.months} months · ${euro(projection.totalInterest)} interest to go`
                  : "The payment doesn't cover interest — the balance grows."}
              </Text>

              <Text style={[styles.tileLabel, { marginTop: 16 }]}>EXTRA MONTHLY PRINCIPAL</Text>
              <View style={styles.chipRow}>
                {EXTRA_MONTHLY_OPTIONS.map((amount) => (
                  <Pressable key={amount} onPress={() => setExtraMonthly(amount)}>
                    <Text style={[styles.chip, extraMonthly === amount && styles.chipActive]}>
                      {amount === 0 ? "None" : `+${euro(amount)}`}
                    </Text>
                  </Pressable>
                ))}
              </View>
              {extraMonthly > 0 && monthsSaved > 0 ? (
                <Text style={styles.savings}>
                  {monthsSaved} months sooner · {euro(interestSaved)} interest saved
                </Text>
              ) : null}
            </View>

            {bars.length > 1 ? (
              <View style={styles.card}>
                <Text style={styles.tileLabel}>BALANCE OVER TIME</Text>
                <View style={styles.chartRow}>
                  {bars.map((point) => (
                    <View key={point.month} style={styles.barWrap}>
                      <Text style={styles.barValue}>{compactEuro(point.remaining)}</Text>
                      <View
                        style={[
                          styles.bar,
                          {
                            height: Math.max((point.remaining / maxBar) * 120, 6),
                            backgroundColor: barColor(point.remaining, settings.balance),
                          },
                        ]}
                      />
                      <Text style={styles.barLabel}>
                        {point.date.toLocaleDateString("en-US", { month: "short" })}
                        {"\n"}
                        {String(point.date.getFullYear()).slice(2)}
                      </Text>
                    </View>
                  ))}
                </View>
              </View>
            ) : null}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, maxWidth: 560, width: "100%", alignSelf: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14 },
  pageTitle: { color: colors.ink, fontSize: 22, fontWeight: "800" },
  editToggle: { color: colors.brandBright, fontSize: 13.5, fontWeight: "700" },
  form: {
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 14, marginBottom: 16,
  },
  fieldLabel: { color: colors.muted, fontSize: 11.5, fontWeight: "600", marginBottom: 4 },
  input: {
    backgroundColor: colors.bg, borderColor: colors.line, borderWidth: 1,
    borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10,
    color: colors.ink, fontSize: 14, marginBottom: 10,
  },
  error: { color: colors.negative, fontSize: 12.5, marginBottom: 8 },
  saveButton: { backgroundColor: colors.brand, borderRadius: 8, paddingVertical: 12, alignItems: "center" },
  saveButtonText: { color: "#fff", fontWeight: "700", fontSize: 14 },
  tileRow: { flexDirection: "row", gap: 10, marginBottom: 14 },
  tile: {
    flex: 1, backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 12,
  },
  tileLabel: { color: colors.muted, fontSize: 9.5, fontWeight: "800", letterSpacing: 0.6 },
  tileValue: { color: colors.ink, fontSize: 16, fontWeight: "800", marginTop: 3 },
  card: {
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 14, marginBottom: 14,
  },
  progressTrack: {
    backgroundColor: colors.line, borderRadius: 5, height: 10,
    overflow: "hidden", marginTop: 10,
  },
  progressFill: { backgroundColor: colors.positive, height: 10 },
  progressText: { color: colors.muted, fontSize: 12, marginTop: 8 },
  payoffDate: { color: colors.ink, fontSize: 24, fontWeight: "800", marginTop: 6 },
  payoffSub: { color: colors.muted, fontSize: 12.5, marginTop: 3 },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  chip: {
    color: colors.muted, fontSize: 12, fontWeight: "600",
    borderColor: colors.line, borderWidth: 1, borderRadius: 20,
    paddingHorizontal: 12, paddingVertical: 6, overflow: "hidden",
  },
  chipActive: { color: "#fff", backgroundColor: colors.brand, borderColor: colors.brand },
  savings: { color: colors.positive, fontSize: 13, fontWeight: "700", marginTop: 10 },
  chartRow: { flexDirection: "row", alignItems: "flex-end", gap: 4, marginTop: 12, height: 168 },
  barWrap: { flex: 1, alignItems: "center", justifyContent: "flex-end" },
  bar: { width: "78%", borderRadius: 4 },
  barValue: { color: colors.muted, fontSize: 8.5, marginBottom: 3 },
  barLabel: { color: colors.muted, fontSize: 9, textAlign: "center", marginTop: 4, lineHeight: 11 },
  empty: { color: colors.muted, fontSize: 13, textAlign: "center", marginTop: 30, lineHeight: 19 },
});
