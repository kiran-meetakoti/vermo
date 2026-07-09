import { Slot } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { ActivityIndicator, View } from "react-native";

import { LoginScreen } from "@/components/login-screen";
import { colors } from "@/constants/vermo";
import { AuthProvider, useAuth } from "@/lib/auth";

function Gate() {
  const { session, loading } = useAuth();
  if (loading) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={colors.brand} size="large" />
      </View>
    );
  }
  // The whole app sits behind authentication; unauthenticated users only
  // ever see the login screen, whatever route they hit.
  return session ? <Slot /> : <LoginScreen />;
}

export default function RootLayout() {
  return (
    <AuthProvider>
      <StatusBar style="light" />
      <Gate />
    </AuthProvider>
  );
}
