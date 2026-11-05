import crypto from "crypto";
import axios from "axios";
import { Config } from "../src/config";

async function probeOptionsWallet() {
  const apiKey = Config.COINSWITCH_API_KEY;
  const secretKey = Config.COINSWITCH_SECRET_KEY;
  const baseUrl = "https://coinswitch.co";

  function signRequest(method: string, endpointPath: string) {
    const epoch = Date.now().toString();
    const message = `${method.toUpperCase()}${endpointPath}${epoch}`;

    const seed = Buffer.from(secretKey, "hex");
    const prefix = Buffer.from("302e020100300506032b657004220420", "hex");
    const pkcs8Key = Buffer.concat([prefix, seed]);
    const privKey = crypto.createPrivateKey({
      key: pkcs8Key,
      format: "der",
      type: "pkcs8",
    });
    const signature = crypto.sign(null, Buffer.from(message, "utf-8"), privKey).toString("hex");

    return {
      "Content-Type": "application/json",
      "X-AUTH-APIKEY": apiKey,
      "X-AUTH-SIGNATURE": signature,
      "X-AUTH-EPOCH": epoch,
    };
  }

  const paths = [
    "/trade/api/v2/futures/wallet_balance",
    "/trade/api/v2/options/wallet_balance",
    "/trade/api/v2/derivatives/wallet_balance",
    "/trade/api/v2/hft/wallet_balance",
    "/trade/api/v2/options/account",
    "/trade/api/v2/user/portfolio",
  ];

  for (const p of paths) {
    try {
      const headers = signRequest("GET", p);
      const res = await axios.get(baseUrl + p, { headers, timeout: 4000 });
      console.log(`✅ ${p} => Status: ${res.status}`, JSON.stringify(res.data));
    } catch (e: any) {
      if (e.response?.status !== 404) {
        console.log(`⚠️ ${p} => Status: ${e.response?.status}`, JSON.stringify(e.response?.data));
      } else {
        console.log(`404 ${p}`);
      }
    }
  }
}

probeOptionsWallet();

