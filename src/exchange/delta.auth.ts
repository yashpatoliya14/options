/**
 * Delta Exchange India Authentication & Request Signing
 * Features automatic server time synchronization to eliminate local clock drift errors.
 */

import crypto from 'crypto';

export class DeltaAuth {
  private static serverTimeOffsetMs: number = 0;

  public static setServerTimeOffset(offsetMs: number): void {
    DeltaAuth.serverTimeOffsetMs = offsetMs;
  }

  public static getServerTimeOffset(): number {
    return DeltaAuth.serverTimeOffsetMs;
  }

  public static getTimestamp(): string {
    return Math.floor((Date.now() + DeltaAuth.serverTimeOffsetMs) / 1000).toString();
  }

  /**
   * Generates headers required for authenticated private REST requests to Delta Exchange India.
   * Signature payload: method + timestamp + path + query_string_or_body
   */
  public static sign(
    apiKey: string,
    apiSecret: string,
    method: string,
    path: string,
    body?: any,
    queryString: string = ''
  ): Record<string, string> {
    const timestamp = DeltaAuth.getTimestamp();
    const queryPart = queryString ? (queryString.startsWith('?') ? queryString : `?${queryString}`) : '';
    const fullPath = `${path}${queryPart}`;
    const payloadStr = body && Object.keys(body).length > 0 ? JSON.stringify(body) : '';

    const message = `${method.toUpperCase()}${timestamp}${fullPath}${payloadStr}`;

    const signature = crypto
      .createHmac('sha256', apiSecret)
      .update(message)
      .digest('hex');

    return {
      'api-key': apiKey,
      'timestamp': timestamp,
      'signature': signature,
      'Content-Type': 'application/json',
      'User-Agent': 'DeltaGoldAlgo/1.0',
    };
  }

  /**
   * Generate signature for WebSocket authentication
   */
  public static signWebSocket(apiKey: string, apiSecret: string): { apiKey: string; signature: string; timestamp: number } {
    const timestamp = Math.floor((Date.now() + DeltaAuth.serverTimeOffsetMs) / 1000);
    const message = `GET${timestamp}/live`;
    const signature = crypto
      .createHmac('sha256', apiSecret)
      .update(message)
      .digest('hex');

    return {
      apiKey,
      signature,
      timestamp,
    };
  }
}
