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

type Mode = "signin" | "signup";

export function LoginScreen() {
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const isSignup = mode === "signup";

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setNotice(null);
    setConfirmPassword("");
  }

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

  async function signUp() {
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    const { data, error: signUpError } = await supabase.auth.signUp({
      email: email.trim().toLowerCase(),
      password,
    });
    setBusy(false);
    if (signUpError) {
      setError(
        signUpError.message.includes("already registered")
          ? "That email already has an account — log in instead."
          : signUpError.message,
      );
      return;
    }
    // Supabase returns a user with an empty identities array when the email
    // is already registered (it won't error, to avoid account enumeration).
    if (data.user && data.user.identities?.length === 0) {
      setError("That email already has an account — log in instead.");
      return;
    }
    if (!data.session) {
      switchMode("signin");
      setNotice(
        "Almost there — we sent a confirmation link to your email. Tap it, then log in here.",
      );
    }
    // If confirmation is disabled, data.session is set and the auth
    // listener signs the user straight in — nothing to do here.
  }

  const submit = isSignup ? signUp : signIn;
  const canSubmit =
    !busy && !!email && !!password && (!isSignup || !!confirmPassword);

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
          autoComplete={isSignup ? "new-password" : "current-password"}
          value={password}
          onChangeText={setPassword}
          onSubmitEditing={isSignup ? undefined : signIn}
        />
        {isSignup ? (
          <TextInput
            style={styles.input}
            placeholder="Confirm password"
            placeholderTextColor={colors.muted}
            secureTextEntry
            autoComplete="new-password"
            value={confirmPassword}
            onChangeText={setConfirmPassword}
            onSubmitEditing={signUp}
          />
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        {notice ? <Text style={styles.notice}>{notice}</Text> : null}
        <Pressable
          style={({ pressed }) => [styles.button, pressed && { opacity: 0.85 }]}
          onPress={submit}
          disabled={!canSubmit}
        >
          {busy ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text style={styles.buttonText}>{isSignup ? "Create account" : "Log in"}</Text>
          )}
        </Pressable>
        <Pressable onPress={() => switchMode(isSignup ? "signin" : "signup")} hitSlop={8}>
          <Text style={styles.switchLink}>
            {isSignup ? "Already have an account? Log in" : "New to Vermo? Create an account"}
          </Text>
        </Pressable>
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
  notice: { color: colors.positive, fontSize: 13, lineHeight: 18, marginBottom: 8 },
  button: {
    backgroundColor: colors.brand, borderRadius: 10, paddingVertical: 14,
    alignItems: "center", marginTop: 6,
  },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 15 },
  switchLink: { color: colors.brandBright, fontSize: 13, fontWeight: "600", textAlign: "center", marginTop: 18 },
});
