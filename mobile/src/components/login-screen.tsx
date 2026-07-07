import { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { colors } from "@/constants/vermo";
import { supabase } from "@/lib/supabase";

export function LoginScreen() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn() {
    setBusy(true);
    setError(null);
    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: email.trim().toLowerCase(),
      password,
    });
    if (signInError) {
      setError(
        signInError.message === "Invalid login credentials"
          ? "Incorrect email or password."
          : signInError.message,
      );
    }
    setBusy(false);
  }

  return (
    <SafeAreaView style={styles.screen}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.container}
      >
        <View style={styles.mark}>
          <Text style={styles.markText}>V</Text>
        </View>
        <Text style={styles.title}>Your wealth,{"\n"}one clear view.</Text>
        <Text style={styles.subtitle}>
          Track investments across India, Europe, and global markets — in one place.
        </Text>

        <TextInput
          style={styles.input}
          placeholder="you@example.com"
          placeholderTextColor={colors.muted}
          autoCapitalize="none"
          autoComplete="email"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
        />
        <TextInput
          style={styles.input}
          placeholder="Password"
          placeholderTextColor={colors.muted}
          secureTextEntry
          autoComplete="current-password"
          value={password}
          onChangeText={setPassword}
          onSubmitEditing={signIn}
        />
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <Pressable
          style={({ pressed }) => [styles.button, pressed && { opacity: 0.85 }]}
          onPress={signIn}
          disabled={busy || !email || !password}
        >
          {busy ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>Log in</Text>
          )}
        </Pressable>
        <Text style={styles.footnote}>
          Accounts are created on the Vermo web app for now.
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  container: { flex: 1, justifyContent: "center", padding: 28, maxWidth: 440, width: "100%", alignSelf: "center" },
  mark: {
    width: 46, height: 46, borderRadius: 12, backgroundColor: colors.brand,
    alignItems: "center", justifyContent: "center", marginBottom: 24,
  },
  markText: { color: "#fff", fontWeight: "900", fontSize: 21 },
  title: { color: colors.ink, fontSize: 30, fontWeight: "800", lineHeight: 36, marginBottom: 10 },
  subtitle: { color: colors.muted, fontSize: 14, lineHeight: 20, marginBottom: 32 },
  input: {
    backgroundColor: colors.panel, borderColor: colors.line, borderWidth: 1.5,
    borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12,
    color: colors.ink, fontSize: 15, marginBottom: 12,
  },
  error: { color: colors.negative, fontSize: 13, marginBottom: 8 },
  button: {
    backgroundColor: colors.brand, borderRadius: 10, paddingVertical: 14,
    alignItems: "center", marginTop: 6,
  },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 15 },
  footnote: { color: colors.muted, fontSize: 12, textAlign: "center", marginTop: 18 },
});
