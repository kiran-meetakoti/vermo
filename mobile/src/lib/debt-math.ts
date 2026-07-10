// Loan payoff math — line-for-line port of finance_math.debt_projection and
// infer_annual_interest_rate so the mobile Debt tab shows the same numbers as
// the web app's Debt tracker. Keep the two in sync.

export type SchedulePoint = { month: number; date: Date; remaining: number };

export type DebtProjection = {
  /** Number.POSITIVE_INFINITY when the payment doesn't reduce the balance. */
  months: number;
  payoffDate: Date | null;
  totalInterest: number;
  schedule: SchedulePoint[];
};

export function addMonths(start: Date, months: number): Date {
  const monthIndex = start.getMonth() + months;
  const year = start.getFullYear() + Math.floor(monthIndex / 12);
  const month = ((monthIndex % 12) + 12) % 12;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  return new Date(year, month, Math.min(start.getDate(), daysInMonth));
}

export function debtProjection(
  balance: number,
  scheduledPayment: number,
  extraMonthly = 0,
  oneTimeExtra = 0,
  annualInterest = 0,
  today: Date = new Date(),
): DebtProjection {
  let remaining = Math.max(balance - oneTimeExtra, 0);
  const monthlyRate = annualInterest / 100 / 12;
  const monthlyPayment = scheduledPayment + extraMonthly;
  let totalInterest = 0;
  let months = 0;
  const schedule: SchedulePoint[] = [];

  if (remaining <= 0) {
    return { months: 0, payoffDate: today, totalInterest: 0, schedule };
  }
  if (monthlyPayment <= 0) {
    return { months: Number.POSITIVE_INFINITY, payoffDate: null, totalInterest: Number.POSITIVE_INFINITY, schedule };
  }

  while (remaining > 0.01 && months < 1200) {
    const interest = remaining * monthlyRate;
    remaining += interest;
    const payment = Math.min(monthlyPayment, remaining);
    remaining -= payment;
    months += 1;
    totalInterest += interest;
    schedule.push({ month: months, date: addMonths(today, months), remaining: Math.max(remaining, 0) });
    if (interest >= monthlyPayment && monthlyRate > 0) {
      return { months: Number.POSITIVE_INFINITY, payoffDate: null, totalInterest: Number.POSITIVE_INFINITY, schedule };
    }
  }

  return { months, payoffDate: addMonths(today, months), totalInterest, schedule };
}

// Same sampling as the web Debt tracker's balance chart: every len/8-th
// point plus the final month, capped at 9 bars.
export function chartPoints(schedule: SchedulePoint[], maxBars = 9): SchedulePoint[] {
  if (schedule.length === 0) return [];
  const step = Math.max(1, Math.floor(schedule.length / 8));
  const points = schedule.filter((_, index) => index % step === 0);
  if (points[points.length - 1] !== schedule[schedule.length - 1]) {
    points.push(schedule[schedule.length - 1]);
  }
  return points.slice(0, maxBars);
}

export function inferAnnualInterestRate(balance: number, payment: number, months: number): number {
  if (months <= 0 || payment <= balance / months) return 0;
  let low = 0;
  let high = 0.05;
  for (let i = 0; i < 100; i += 1) {
    const monthlyRate = (low + high) / 2;
    const impliedPayment = (balance * monthlyRate) / (1 - (1 + monthlyRate) ** -months);
    if (impliedPayment < payment) {
      low = monthlyRate;
    } else {
      high = monthlyRate;
    }
  }
  const monthlyRate = (low + high) / 2;
  return ((1 + monthlyRate) ** 12 - 1) * 100;
}
