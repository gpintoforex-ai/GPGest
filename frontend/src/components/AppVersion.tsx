import { version } from "../../package.json";

export default function AppVersion() {
  return <small aria-label={`Versão da IAZELA: ${version}`}>Versão {version}</small>;
}
