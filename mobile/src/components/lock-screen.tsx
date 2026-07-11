import { useEffect } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors } from "@/constants/vermo";
import { authenticate } from "@/lib/biometric";

export function LockScreen({ onUnlock }: { onUnlock: () => void }) {
  async function tryUnlock() {
    if (await authenticate()) onUnlock();
  }

  // Prompt immediately on mount — the button is the retry path.
  useEffect(() => {
    tryUnlock();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.container}>
        <View style={styles.mark}>
          <Text style={styles.markText}>V</Text>
        </View>
        <Text style={styles.title}>Vermo is locked</Text>
        <Text style={styles.subtitle}>Your finances stay private on this device.</Text>
        <Pressable style={styles.button} onPress={tryUnlock}>
          <Text style={styles.buttonText}>Unlock</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  container: { flex: 1, alignItems: "center", justifyContent: "center", padding: 28 },
  mark: {
    width: 56, height: 56, borderRadius: 14, backgroundColor: colors.brand,
    alignItems: "center", justifyContent: "center", marginBottom: 20,
  },
  markText: { color: "#fff", fontWeight: "900", fontSize: 26 },
  title: { color: colors.ink, fontSize: 20, fontWeight: "800", marginBottom: 6 },
  subtitle: { color: colors.muted, fontSize: 13.5, marginBottom: 28 },
  button: {
    backgroundColor: colors.brand, borderRadius: 10,
    paddingVertical: 13, paddingHorizontal: 44,
  },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 15 },
});
