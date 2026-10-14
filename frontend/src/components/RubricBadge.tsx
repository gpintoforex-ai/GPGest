import { Tag, Receipt, Wallet, Droplet, Zap, Phone, ShoppingCart, Landmark, Shield, Users, Package, Truck, Briefcase, Coffee, Fuel } from "lucide-react";

export const rubricIcons = {
  tag: ["Etiqueta", Tag], receipt: ["Documento", Receipt], wallet: ["Caixa", Wallet], droplet: ["Água", Droplet], zap: ["Eletricidade", Zap], phone: ["Comunicações", Phone],
  "shopping-cart": ["Compras", ShoppingCart], landmark: ["Banco / impostos", Landmark], shield: ["Seguros", Shield], users: ["Pessoas", Users], package: ["Mercadorias", Package], truck: ["Transportes", Truck], briefcase: ["Trabalho", Briefcase], coffee: ["Refeições", Coffee], fuel: ["Combustível", Fuel],
} as const;

export default function RubricBadge({ name, icon = "tag", color = "#245fc5" }: { name: string; icon?: string; color?: string }) {
  const Icon = (rubricIcons[icon as keyof typeof rubricIcons] || rubricIcons.tag)[1];
  return <span className="rubric-badge"><span className="rubric-icon" style={{ color, borderColor: color, backgroundColor: `${color}12` }}><Icon size={18} aria-hidden="true" /></span><span>{name}</span></span>;
}
