import { Banknote, CreditCard, Smartphone, ArrowLeftRight, FileText, Wallet } from "lucide-react";
import { paymentTone } from "../lib/paymentTone";

export default function PaymentIdentity({ name, code }: { name: string; code?: string }) {
  const tone = paymentTone(code || name);
  const Icon = tone.endsWith("-numerario") ? Banknote : tone.endsWith("-cartao") ? CreditCard
    : tone.endsWith("-mbway") ? Smartphone : tone.endsWith("-transferencia") ? ArrowLeftRight
    : tone.endsWith("-cheque") ? FileText : Wallet;
  return <span className={`record-payment-label ${tone}`}><Icon size={15} aria-hidden="true" /><span>{name}</span></span>;
}
