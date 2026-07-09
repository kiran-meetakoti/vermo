import { useMemo } from "react";
import { StyleSheet, Text, View } from "react-native";
import Svg, { Defs, LinearGradient, Path, Stop } from "react-native-svg";

import { colors, euro } from "@/constants/vermo";

export type SnapshotPoint = { snapshot_date: string; net_worth_eur: number };

const WIDTH = 1000; // viewBox units; SVG scales to container width
const HEIGHT = 220;
const PAD = 8;

export function NetWorthChart({ points }: { points: SnapshotPoint[] }) {
  const geometry = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map((p) => Number(p.net_worth_eur));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const stepX = (WIDTH - PAD * 2) / (points.length - 1);
    const coords = values.map((value, index) => ({
      x: PAD + index * stepX,
      y: PAD + (1 - (value - min) / span) * (HEIGHT - PAD * 2),
    }));
    const line = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
    const area = `${line} L${coords[coords.length - 1].x.toFixed(1)},${HEIGHT} L${coords[0].x.toFixed(1)},${HEIGHT} Z`;
    return {
      line,
      area,
      rising: values[values.length - 1] >= values[0],
      min,
      max,
      first: points[0].snapshot_date,
      last: points[points.length - 1].snapshot_date,
    };
  }, [points]);

  if (!geometry) return null;
  const stroke = geometry.rising ? colors.positive : colors.negative;

  return (
    <View style={styles.card}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>NET WORTH</Text>
        <Text style={styles.range}>
          {geometry.first} → {geometry.last}
        </Text>
      </View>
      <Svg width="100%" height={160} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} preserveAspectRatio="none">
        <Defs>
          <LinearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0" stopColor={stroke} stopOpacity="0.28" />
            <Stop offset="1" stopColor={stroke} stopOpacity="0.02" />
          </LinearGradient>
        </Defs>
        <Path d={geometry.area} fill="url(#fill)" />
        <Path d={geometry.line} stroke={stroke} strokeWidth={3} fill="none" />
      </Svg>
      <View style={styles.headerRow}>
        <Text style={styles.bound}>low {euro(geometry.min)}</Text>
        <Text style={styles.bound}>high {euro(geometry.max)}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1,
    borderRadius: 12, padding: 14, marginBottom: 18,
  },
  headerRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  title: { color: colors.muted, fontSize: 10.5, fontWeight: "800", letterSpacing: 0.8 },
  range: { color: colors.muted, fontSize: 11 },
  bound: { color: colors.muted, fontSize: 11, marginTop: 4 },
});
