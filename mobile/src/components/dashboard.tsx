import { useCallback, useEffect, useState } from "react";
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { NetWorthChart, type SnapshotPoint } from "@/components/net-worth-chart";
import { colors, euro, percent } from "@/constants/vermo";
import { useAuth } from "@/lib/auth";
import { supabase } from "@/lib/supabase";

type Holding = {
  id: string;
  name: string;
  ticker: string;
  market: string;
  value_eur: number;
  invested_eur: number;
  return_percent: number;
};

export function Dashboard() {
  const { session } = useAuth();
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [snapshots, setSnapshots] = useState<SnapshotPoint[]>([]);
  const [otherAssetsTotal, setOtherAssetsTotal] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    // RLS scopes every query to the signed-in user automatically.
    const [holdingsResult, assetsResult, snapshotsResult] = await Promise.all([
      supabase
        .from("holdings")
        .select("id, name, ticker, market, value_eur, invested_eur, return_percent")
        .order("value_eur", { ascending: false }),
      supabase.from("manual_assets").select("value_eur"),
      supabase
        .from("snapshots")
        .select("snapshot_date, net_worth_eur")
        .eq("market", "All")
        .order("snapshot_date", { ascending: true }),
    ]);
    if (holdingsResult.error || assetsResult.error || snapshotsResult.error) {
      setError((holdingsResult.error ?? assetsResult.error ?? snapshotsResult.error)!.message);
      return;
    }
    setHoldings((holdingsResult.data ?? []) as Holding[]);
    setSnapshots((snapshotsResult.data ?? []) as SnapshotPoint[]);
    setOtherAssetsTotal(
      (assetsResult.data ?? []).reduce((sum, row: any) => sum + Number(row.value_eur ?? 0), 0),
    );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const portfolioValue = holdings.reduce((sum, h) => sum + Number(h.value_eur ?? 0), 0);
  const invested = holdings.reduce((sum, h) => sum + Number(h.invested_eur ?? 0), 0);
  const profit = portfolioValue - invested;
  const displayName = session?.user.user_metadata?.display_name ?? session?.user.email ?? "";

  return (
    <SafeAreaView style={styles.screen}>
      <FlatList
        data={holdings}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />
        }
        ListHeaderComponent={
          <View>
            <View style={styles.headerRow}>
              <View>
                <Text style={styles.brand}>Vermo</Text>
                <Text style={styles.signedIn}>{displayName}</Text>
              </View>
              <Pressable onPress={() => supabase.auth.signOut()} hitSlop={10}>
                <Text style={styles.signOut}>Sign out</Text>
              </Pressable>
            </View>

            <View style={styles.heroCard}>
              <Text style={styles.heroLabel}>TOTAL WEALTH</Text>
              <Text style={styles.heroValue}>{euro(portfolioValue + otherAssetsTotal)}</Text>
              <Text style={styles.heroSub}>
                portfolio {euro(portfolioValue)} · other assets {euro(otherAssetsTotal)}
              </Text>
            </View>

            <View style={styles.tileRow}>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>PROFIT / LOSS</Text>
                <Text style={[styles.tileValue, { color: profit >= 0 ? colors.positive : colors.negative }]}>
                  {profit >= 0 ? "+" : ""}
                  {euro(profit)}
                </Text>
                <Text style={styles.tileSub}>
                  {invested ? percent((profit / invested) * 100) : "—"} on invested
                </Text>
              </View>
              <View style={styles.tile}>
                <Text style={styles.tileLabel}>POSITIONS</Text>
                <Text style={styles.tileValue}>{holdings.length}</Text>
                <Text style={styles.tileSub}>across India + Global</Text>
              </View>
            </View>

            <NetWorthChart points={snapshots} />

            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Text style={styles.sectionTitle}>Holdings</Text>
          </View>
        }
        renderItem={({ item }) => (
          <View style={styles.holdingRow}>
            <View style={styles.holdingLeft}>
              <Text style={styles.holdingName} numberOfLines={1}>
                {item.name}
              </Text>
              <Text style={styles.holdingTicker}>
                {item.ticker} · {item.market}
              </Text>
            </View>
            <View style={styles.holdingRight}>
              <Text style={styles.holdingValue}>{euro(Number(item.value_eur ?? 0))}</Text>
              <Text
                style={{
                  fontSize: 12,
                  color: Number(item.return_percent ?? 0) >= 0 ? colors.positive : colors.negative,
                }}
              >
                {percent(Number(item.return_percent ?? 0))}
              </Text>
            </View>
          </View>
        )}
        ListEmptyComponent={
          <Text style={styles.empty}>No holdings yet — add your first on the web app.</Text>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 20, maxWidth: 560, width: "100%", alignSelf: "center" },
  headerRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 18 },
  brand: { color: colors.ink, fontSize: 22, fontWeight: "800" },
  signedIn: { color: colors.muted, fontSize: 12, marginTop: 2 },
  signOut: { color: colors.brandBright, fontSize: 13, fontWeight: "600" },
  heroCard: {
    backgroundColor: colors.brand, borderRadius: 14, padding: 20, marginBottom: 12,
  },
  heroLabel: { color: "#d3efe6", fontSize: 11, fontWeight: "700", letterSpacing: 1 },
  heroValue: { color: "#ffffff", fontSize: 34, fontWeight: "800", marginVertical: 4 },
  heroSub: { color: "#d3efe6", fontSize: 12.5 },
  tileRow: { flexDirection: "row", gap: 12, marginBottom: 18 },
  tile: {
    flex: 1, backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 14,
  },
  tileLabel: { color: colors.muted, fontSize: 10.5, fontWeight: "700", letterSpacing: 0.8 },
  tileValue: { color: colors.ink, fontSize: 20, fontWeight: "800", marginVertical: 3 },
  tileSub: { color: colors.muted, fontSize: 11.5 },
  sectionTitle: { color: colors.ink, fontSize: 15, fontWeight: "700", marginBottom: 10 },
  holdingRow: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12, marginBottom: 8,
  },
  holdingLeft: { flex: 1, marginRight: 12 },
  holdingName: { color: colors.ink, fontSize: 14, fontWeight: "600" },
  holdingTicker: { color: colors.muted, fontSize: 11.5, marginTop: 2 },
  holdingRight: { alignItems: "flex-end" },
  holdingValue: { color: colors.ink, fontSize: 14, fontWeight: "700" },
  error: { color: colors.negative, fontSize: 13, marginBottom: 10 },
  empty: { color: colors.muted, fontSize: 13, textAlign: "center", marginTop: 30 },
});
