import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, euro } from "@/constants/vermo";
import { useAuth } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

type Expense = {
  id: string;
  name: string;
  category: string;
  amount_eur: number;
  expense_date: string;
};

// Same category set the web app's auto-categorizer produces.
const CATEGORIES = [
  "Groceries", "Eating out", "Transport", "Rent", "Utilities",
  "Subscriptions", "Shopping", "Insurance", "Travel", "Other",
];

export default function BudgetTab() {
  const { session } = useAuth();
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [salary, setSalary] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formAmount, setFormAmount] = useState("");
  const [formCategory, setFormCategory] = useState("Groceries");
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const [expensesResult, salaryResult] = await Promise.all([
      supabase
        .from("budget_expenses")
        .select("id, name, category, amount_eur, expense_date")
        .order("expense_date", { ascending: false })
        .limit(200),
      supabase.from("budget_settings").select("value").eq("key", "monthly_salary").maybeSingle(),
    ]);
    setExpenses((expensesResult.data ?? []) as Expense[]);
    setSalary(Number(salaryResult.data?.value ?? 0));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const monthPrefix = new Date().toISOString().slice(0, 7);
  const thisMonth = useMemo(
    () => expenses.filter((e) => (e.expense_date ?? "").startsWith(monthPrefix)),
    [expenses, monthPrefix],
  );
  const spent = thisMonth.reduce((total, e) => total + Number(e.amount_eur), 0);
  const remaining = salary - spent;

  const byCategory = useMemo(() => {
    const totals = new Map<string, number>();
    for (const e of thisMonth) {
      totals.set(e.category, (totals.get(e.category) ?? 0) + Number(e.amount_eur));
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);
  }, [thisMonth]);

  async function addExpense() {
    const amount = parseFloat(formAmount.replace(",", "."));
    if (!formName.trim() || !Number.isFinite(amount) || amount <= 0) {
      setFormError("A description and a positive amount are required.");
      return;
    }
    setSaving(true);
    setFormError(null);
    // RLS WITH CHECK requires user_id = auth.uid() — set it explicitly.
    const { error } = await supabase.from("budget_expenses").insert({
      user_id: session!.user.id,
      name: formName.trim(),
      category: formCategory,
      amount_eur: amount,
      expense_date: new Date().toISOString().slice(0, 10),
    });
    setSaving(false);
    if (error) {
      setFormError(error.message);
      return;
    }
    setFormName("");
    setFormAmount("");
    setShowForm(false);
    await load();
  }

  return (
    <SafeAreaView style={styles.screen}>
      <FlatList
        data={expenses.slice(0, 40)}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />}
        ListHeaderComponent={
          <View>
            <View style={styles.headerRow}>
              <Text style={styles.pageTitle}>Budget</Text>
              <Pressable onPress={() => setShowForm((v) => !v)} hitSlop={10}>
                <Text style={styles.addToggle}>{showForm ? "Close" : "+ Add expense"}</Text>
              </Pressable>
            </View>

            {showForm ? (
              <View style={styles.form}>
                <TextInput
                  style={styles.input}
                  placeholder="Description (e.g. REWE)"
                  placeholderTextColor={colors.muted}
                  value={formName}
                  onChangeText={setFormName}
                />
                <TextInput
                  style={styles.input}
                  placeholder="Amount in EUR"
                  placeholderTextColor={colors.muted}
                  keyboardType="decimal-pad"
                  value={formAmount}
                  onChangeText={setFormAmount}
                />
                <View style={styles.chipRow}>
                  {CATEGORIES.map((category) => (
                    <Pressable key={category} onPress={() => setFormCategory(category)}>
                      <Text style={[styles.chip, formCategory === category && styles.chipActive]}>
                        {category}
                      </Text>
                    </Pressable>
                  ))}
                </View>
                {formError ? <Text style={styles.error}>{formError}</Text> : null}
                <Pressable style={styles.saveButton} onPress={addExpense} disabled={saving}>
                  <Text style={styles.saveButtonText}>{saving ? "Saving…" : "Save expense"}</Text>
                </Pressable>
              </View>
            ) : null}

            <View style={styles.tileRow}>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>SALARY</Text>
                <Text style={styles.tileValue}>{euro(salary)}</Text>
              </View>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>SPENT THIS MONTH</Text>
                <Text style={[styles.tileValue, { color: colors.negative }]}>{euro(spent)}</Text>
              </View>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>REMAINING</Text>
                <Text style={[styles.tileValue, { color: remaining >= 0 ? colors.positive : colors.negative }]}>
                  {euro(remaining)}
                </Text>
              </View>
            </View>

            {byCategory.length > 0 ? (
              <View style={styles.categoryCard}>
                <Text style={styles.tileLabel}>TOP CATEGORIES · THIS MONTH</Text>
                {byCategory.map(([category, total]) => (
                  <View key={category} style={styles.categoryRow}>
                    <Text style={styles.categoryName}>{category}</Text>
                    <View style={styles.categoryTrack}>
                      <View
                        style={[styles.categoryFill, { width: `${Math.min((total / (byCategory[0][1] || 1)) * 100, 100)}%` }]}
                      />
                    </View>
                    <Text style={styles.categoryValue}>{euro(total)}</Text>
                  </View>
                ))}
              </View>
            ) : null}

            <Text style={styles.sectionTitle}>Recent expenses</Text>
          </View>
        }
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={{ flex: 1, marginRight: 12 }}>
              <Text style={styles.rowName} numberOfLines={1}>{item.name}</Text>
              <Text style={styles.rowSub}>
                {item.category} · {(item.expense_date ?? "").slice(0, 10)}
              </Text>
            </View>
            <Text style={styles.rowValue}>−{euro(Number(item.amount_eur))}</Text>
          </View>
        )}
        ListEmptyComponent={
          <Text style={styles.empty}>No expenses yet — add one above, or import a bank statement on the web app.</Text>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, maxWidth: 560, width: "100%", alignSelf: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 14 },
  pageTitle: { color: colors.ink, fontSize: 22, fontWeight: "800" },
  addToggle: { color: colors.brandBright, fontSize: 13.5, fontWeight: "700" },
  form: {
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 14, marginBottom: 16,
  },
  input: {
    backgroundColor: colors.bg, borderColor: colors.line, borderWidth: 1,
    borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10,
    color: colors.ink, fontSize: 14, marginBottom: 10,
  },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 10 },
  chip: {
    color: colors.muted, fontSize: 12, fontWeight: "600",
    borderColor: colors.line, borderWidth: 1, borderRadius: 20,
    paddingHorizontal: 10, paddingVertical: 5, overflow: "hidden",
  },
  chipActive: { color: "#fff", backgroundColor: colors.brand, borderColor: colors.brand },
  saveButton: { backgroundColor: colors.brand, borderRadius: 8, paddingVertical: 12, alignItems: "center" },
  saveButtonText: { color: "#fff", fontWeight: "700", fontSize: 14 },
  error: { color: colors.negative, fontSize: 12.5, marginBottom: 8 },
  tileRow: { flexDirection: "row", gap: 10, marginBottom: 14 },
  tile: {
    flex: 1, backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 12,
  },
  tileLabel: { color: colors.muted, fontSize: 9.5, fontWeight: "800", letterSpacing: 0.6 },
  tileValue: { color: colors.ink, fontSize: 16, fontWeight: "800", marginTop: 3 },
  categoryCard: {
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 14, marginBottom: 16,
  },
  categoryRow: { flexDirection: "row", alignItems: "center", marginTop: 10, gap: 8 },
  categoryName: { color: colors.ink, fontSize: 12.5, width: 96 },
  categoryTrack: { flex: 1, backgroundColor: colors.line, borderRadius: 4, height: 6, overflow: "hidden" },
  categoryFill: { backgroundColor: colors.brandBright, height: 6 },
  categoryValue: { color: colors.muted, fontSize: 12, width: 70, textAlign: "right" },
  sectionTitle: { color: colors.ink, fontSize: 15, fontWeight: "700", marginBottom: 10 },
  row: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 8,
  },
  rowName: { color: colors.ink, fontSize: 14, fontWeight: "600" },
  rowSub: { color: colors.muted, fontSize: 11.5, marginTop: 2 },
  rowValue: { color: colors.negative, fontSize: 14, fontWeight: "700" },
  empty: { color: colors.muted, fontSize: 13, textAlign: "center", marginTop: 30 },
});
