import { ActivityIndicator, View } from "react-native";

import { Dashboard } from "@/components/dashboard";
import { LoginScreen } from "@/components/login-screen";
import { colors } from "@/constants/vermo";
import { useAuth } from "@/lib/auth";

export default function Index() {
  const { session, loading } = useAuth();

  if (loading) {
    return (
      <View style={{ flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator color={colors.brand} size="large" />
      </View>
    );
  }
  return session ? <Dashboard /> : <LoginScreen />;
}
