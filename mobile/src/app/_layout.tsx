import { Slot } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";

import { LockScreen } from "@/components/lock-screen";
import { LoginScreen } from "@/components/login-screen";
import { colors } from "@/constants/vermo";
import { AuthProvider, useAuth } from "@/lib/auth";
import { biometricAvailable, biometricEnabled } from "@/lib/biometric";

function Gate() {
  const { session, loading } = useAuth();
  // null = still deciding. The lock applies only on cold start with a
  // restored session — someone who just typed their password shouldn't be
  // asked for Face ID on top.
  const [locked, setLocked] = useState<boolean | null>(null);
  const evaluatedOnce = useRef(false);

  useEffect(() => {
    if (loading || evaluatedOnce.current) return;
    evaluatedOnce.current = true;
    if (!session) {
      setLocked(false);
      return;
    }
    (async () => {
      setLocked((await biometricAvailable()) && (await biometricEnabled()));
    })();
  }, [loading, session]);

  if (loading || locked === null) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={colors.brand} size="large" />
      </View>
    );
  }
  if (!session) return <LoginScreen />;
  if (locked) return <LockScreen onUnlock={() => setLocked(false)} />;
  return <Slot />;
}

export default function RootLayout() {
  return (
    <AuthProvider>
      <StatusBar style="light" />
      <Gate />
    </AuthProvider>
  );
}
