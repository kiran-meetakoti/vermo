import AsyncStorage from "@react-native-async-storage/async-storage";
import * as LocalAuthentication from "expo-local-authentication";
import { Platform } from "react-native";

const STORAGE_KEY = "vermo.biometric.enabled";

/** True when the device has enrolled Face ID / Touch ID / fingerprint.
 * Always false on web — the lock only makes sense on a device. */
export async function biometricAvailable(): Promise<boolean> {
  if (Platform.OS === "web") return false;
  const [hasHardware, isEnrolled] = await Promise.all([
    LocalAuthentication.hasHardwareAsync(),
    LocalAuthentication.isEnrolledAsync(),
  ]);
  return hasHardware && isEnrolled;
}

export async function biometricEnabled(): Promise<boolean> {
  return (await AsyncStorage.getItem(STORAGE_KEY)) === "true";
}

export async function setBiometricEnabled(enabled: boolean): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, enabled ? "true" : "false");
}

/** Prompt Face ID / fingerprint. Falls back to the device passcode, so a
 * failed face scan doesn't lock the owner out of their own numbers. */
export async function authenticate(): Promise<boolean> {
  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: "Unlock Vermo",
    fallbackLabel: "Use passcode",
  });
  return result.success;
}
