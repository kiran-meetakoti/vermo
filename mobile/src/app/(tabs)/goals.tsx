import { useCallback, useEffect, useState } from "react";
import { FlatList, RefreshControl, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors, euro } from "@/constants/vermo";
import { supabase } from "@/lib/supabase";

type Goal = {
  id: string;
  name: string;
  target_eur: number;
  target_date: string;
  monthly_contribution: number;
};

export default function GoalsTab() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [totalWealth, setTotalWealth] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    const [goalsResult, holdingsResult, assetsResult] = await Promise.all([
      supabase.from("goals").select("id, name, target_eur, target_date, monthly_contribution").order("target_date"),
      supabase.from("holdings").select("value_eur"),
      supabase.from("manual_assets").select("value_eur"),
    ]);
    setGoals((goalsResult.data ?? []) as Goal[]);
    const sum = (rows: any[] | null) => (rows ?? []).reduce((total, r) => total + Number(r.value_eur ?? 0), 0);
    setTotalWealth(sum(holdingsResult.data) + sum(assetsResult.data));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  function monthsLeft(targetDate: string): number {
    const target = new Date(targetDate);
    const now = new Date();
    return Math.max((target.getFullYear() - now.getFullYear()) * 12 + target.getMonth() - now.getMonth(), 0);
  }

  return (
    <SafeAreaView style={styles.screen}>
      <FlatList
        data={goals}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />}
        ListHeaderComponent={
          <View>
            <Text style={styles.pageTitle}>Goals</Text>
            <Text style={styles.subtitle}>
              Measured against total wealth ({euro(totalWealth)}). Goals are lenses, not allocations.
            </Text>
          </View>
        }
        renderItem={({ item }) => {
          const progress = item.target_eur ? Math.min((totalWealth / Number(item.target_eur)) * 100, 100) : 0;
          const reached = totalWealth >= Number(item.target_eur);
          return (
            <View style={styles.card}>
              <View style={styles.cardHeader}>
                <Text style={styles.cardName}>{item.name}</Text>
                <Text style={[styles.badge, reached ? styles.badgeGreen : styles.badgeNeutral]}>
                  {reached ? "Reached 🎉" : `${monthsLeft(item.target_date)} months left`}
                </Text>
              </View>
              <View style={styles.track}>
                <View style={[styles.fill, { width: `${progress}%` }]} />
              </View>
              <Text style={styles.cardSub}>
                {euro(totalWealth)} of {euro(Number(item.target_eur))} ({progress.toFixed(0)}%)
                {Number(item.monthly_contribution) > 0 ? ` · ${euro(Number(item.monthly_contribution))}/mo planned` : ""}
              </Text>
            </View>
          );
        }}
        ListEmptyComponent={
          <Text style={styles.empty}>No goals yet — create your first on the web app.</Text>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, maxWidth: 560, width: "100%", alignSelf: "center" },
  pageTitle: { color: colors.ink, fontSize: 22, fontWeight: "800", marginBottom: 6 },
  subtitle: { color: colors.muted, fontSize: 12.5, marginBottom: 16, lineHeight: 18 },
  card: {
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 16, marginBottom: 10,
  },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 10 },
  cardName: { color: colors.ink, fontSize: 15, fontWeight: "700", flex: 1, marginRight: 10 },
  badge: { fontSize: 11.5, fontWeight: "700" },
  badgeGreen: { color: colors.positive },
  badgeNeutral: { color: colors.muted },
  track: { backgroundColor: colors.line, borderRadius: 6, height: 8, overflow: "hidden", marginBottom: 8 },
  fill: { backgroundColor: colors.brandBright, height: 8 },
  cardSub: { color: colors.muted, fontSize: 12 },
  empty: { color: colors.muted, fontSize: 13, textAlign: "center", marginTop: 30 },
});
