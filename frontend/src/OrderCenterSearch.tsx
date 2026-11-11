import { useRef } from "react";
import { Search } from "lucide-react";
import {
  updateLocationParam,
  useLocationSearch,
} from "./components/WorkspaceUI";

export default function OrderCenterSearch() {
  const input = useRef<HTMLInputElement>(null);
  const query =
    new URLSearchParams(useLocationSearch()).get("pesquisa.encomendas") || "";
  return (
    <div
      className="order-center-search order-center-local-search"
      data-ignore-dirty
    >
      <Search size={16} aria-hidden="true" />
      <input
        ref={input}
        type="search"
        aria-label="Pesquisar no centro de encomendas"
        placeholder="Filtrar este painel…"
        value={query}
        onChange={(event) =>
          updateLocationParam("pesquisa.encomendas", event.target.value, true)
        }
      />
    </div>
  );
}
