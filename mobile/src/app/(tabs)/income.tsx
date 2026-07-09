import { useCallback, useEffect, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, euro } from "@/constants/vermo";
import { supabase } from "@/lib/supabase";

type IncomeEvent = {
  id: string;
  source_type: string;
  name: string;
  amount_eur: number;
  income_date: string;
  notes: string | null;
};

// Display-level aggregation only — mirrors income_db.income_summary's windows.
// Anything more financial than date-window sums belongs to the Python backend.
function summarize(events: IncomeEvent[]) {
  const now = new Date();
  const yearStart = `${now.getFullYear()}-01-01`;
  const trailingStart = new Date(now.getTime() - 365 * 24 * 3600 * 1000).toISOString().slice(0, 10);
  const trailing = events.filter((e) => e.income_date >= trailingStart);
  const sum = (rows: IncomeEvent[]) => rows.reduce((total, r) => total + Number(r.amount_eur), 0);
  return {
    trailing12m: sum(trailing),
    thisYear: sum(events.filter((e) => e.income_date >= yearStart)),
    monthlyAvg: sum(trailing) / 12,
  };
}

export default function IncomeTab() {
  const [events, setEvents] = useState<IncomeEvent[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const { data } = await supabase
      .from("income_events")
      .select("id, source_type, name, amount_eur, income_date, notes")
      .order("income_date", { ascending: false })
      .limit(100);
    setEvents((data ?? []) as IncomeEvent[]);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const summary = summarize(events);

  return (
    <SafeAreaView style={styles.screen}>
      <FlatList
        data={events}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />}
        ListHeaderComponent={
          <View>
            <Text style={styles.pageTitle}>Income</Text>
            <View style={styles.tileRow}>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>LAST 12 MONTHS</Text>
                <Text style={styles.tileValue}>{euro(summary.trailing12m)}</Text>
              </View>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>THIS YEAR</Text>
                <Text style={styles.tileValue}>{euro(summary.thisYear)}</Text>
              </View>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>MONTHLY AVG</Text>
                <Text style={styles.tileValue}>{euro(summary.monthlyAvg)}</Text>
              </View>
            </View>
            <Text style={styles.sectionTitle}>Recent</Text>
          </View>
        }
        renderItem={({ item }) => (
          <View style={styles.row}>
            <View style={{ flex: 1, marginRight: 12 }}>
              <Text style={styles.rowName} numberOfLines={1}>{item.name}</Text>
              <Text style={styles.rowSub}>
                {item.source_type} · {item.income_date.slice(0, 10)}
              </Text>
            </View>
            <Text style={styles.rowValue}>{euro(Number(item.amount_eur))}</Text>
          </View>
        )}
        ListEmptyComponent={
          <Text style={styles.empty}>No income recorded yet — add dividends, interest, or rent on the web app.</Text>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, maxWidth: 560, width: "100%", alignSelf: "center" },
  pageTitle: { color: colors.ink, fontSize: 22, fontWeight: "800", marginBottom: 14 },
  tileRow: { flexDirection: "row", gap: 10, marginBottom: 18 },
  tile: {
    flex: 1, backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 12,
  },
  tileLabel: { color: colors.muted, fontSize: 9.5, fontWeight: "800", letterSpacing: 0.6 },
  tileValue: { color: colors.ink, fontSize: 16, fontWeight: "800", marginTop: 3 },
  sectionTitle: { color: colors.ink, fontSize: 15, fontWeight: "700", marginBottom: 10 },
  row: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 8,
  },
  rowName: { color: colors.ink, fontSize: 14, fontWeight: "600" },
  rowSub: { color: colors.muted, fontSize: 11.5, marginTop: 2 },
  rowValue: { color: colors.positive, fontSize: 14, fontWeight: "700" },
  empty: { color: colors.muted, fontSize: 13, textAlign: "center", marginTop: 30 },
});
