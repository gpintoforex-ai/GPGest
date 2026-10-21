// Servidor de desenvolvimento em HTTPS para a rede local (Iniciar-Rede.ps1 -Https).
// Os navegadores só permitem a câmara em direto em contexto seguro; o certificado é local e autoassinado
// (scripts/lan-certificate.py, em .local/https). A configuração por omissão (vite.config.ts) não muda.
import { readFileSync } from "node:fs";
import { mergeConfig } from "vite";
import base from "./vite.config.ts";

const key = process.env.GPGEST_HTTPS_KEY, cert = process.env.GPGEST_HTTPS_CERT;
if (!key || !cert) throw new Error("Defina GPGEST_HTTPS_KEY e GPGEST_HTTPS_CERT ou use Iniciar-Rede.ps1 -Https.");

export default mergeConfig(base, { server: { https: { key: readFileSync(key), cert: readFileSync(cert) } } });
