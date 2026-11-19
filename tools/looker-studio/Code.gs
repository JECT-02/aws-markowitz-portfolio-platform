/**
 * Looker Studio community connector for the Markowitz portfolio Reader API.
 *
 * Runs entirely in Google's Apps Script sandbox; it never touches AWS
 * credentials, only the read-only HTTP API fronted by Cognito. Configuration
 * lives in Script Properties (app-wide, set once by the maintainer):
 *   API_ENDPOINT   base URL of the API Gateway reader (https://...execute-api...amazonaws.com)
 *   COGNITO_DOMAIN Cognito hosted UI domain with scheme (https://xxx.auth.us-east-1.amazoncognito.com)
 *   CLIENT_ID      confidential app client id (terraform output looker_connector_client_id)
 *   CLIENT_SECRET  confidential app client secret (terraform output looker_connector_client_secret)
 *
 * Google's connector guidance forbids reading user credentials from config
 * fields, so the OAuth flow below uses the official apps-script-oauth2
 * library against Cognito's /oauth2/authorize and /oauth2/token endpoints
 * (authorization code grant, confidential client, refresh token capped at
 * 90 days by Terraform).
 */

var cc = DataStudioApp.createCommunityConnector();

var TABLES = {
  weights: {
    label: "Proposed vs actual vs benchmark weights",
    path: "/proposal",
    fields: function (fields, types, aggregations) {
      fields.newDimension().setId("run_date").setName("Run date").setType(types.YEAR_MONTH_DAY);
      fields.newDimension().setId("proposal_id").setName("Proposal").setType(types.TEXT);
      fields.newDimension().setId("status").setName("Status").setType(types.TEXT);
      fields.newDimension().setId("mode").setName("Allocation mode").setType(types.TEXT);
      fields.newDimension().setId("ticker").setName("Ticker").setType(types.TEXT);
      fields
        .newMetric()
        .setId("weight")
        .setName("Weight")
        .setType(types.NUMBER)
        .setAggregation(aggregations.MAX);
      fields.newMetric().setId("drift_l1").setName("Drift L1").setType(types.NUMBER).setAggregation(aggregations.MAX);
      return fields;
    },
    rows: function (document) {
      var rows = [];
      var modes = { optimizer: document.weights, actual: document.actual_weights };
      var benchmarks = document.benchmark_weights || {};
      Object.keys(benchmarks).forEach(function (mode) {
        modes[mode] = benchmarks[mode];
      });
      Object.keys(modes).forEach(function (mode) {
        var allocation = modes[mode] || {};
        Object.keys(allocation).forEach(function (ticker) {
          rows.push(_row(document, [document.run_date, document.proposal_id, document.status, mode, ticker, allocation[ticker], document.drift_l1]));
        });
      });
      return rows;
    },
  },
  metrics: {
    label: "Portfolio metrics by allocation",
    path: "/metrics",
    fields: function (fields, types, aggregations) {
      fields.newDimension().setId("run_date").setName("Run date").setType(types.YEAR_MONTH_DAY);
      fields.newDimension().setId("proposal_id").setName("Proposal").setType(types.TEXT);
      fields.newDimension().setId("mode").setName("Allocation mode").setType(types.TEXT);
      fields.newDimension().setId("metric").setName("Metric").setType(types.TEXT);
      fields.newMetric().setId("value").setName("Value").setType(types.NUMBER).setAggregation(aggregations.MAX);
      return fields;
    },
    rows: function (document) {
      var rows = [];
      var metrics = document.metrics || {};
      Object.keys(metrics).forEach(function (mode) {
        var values = metrics[mode] || {};
        Object.keys(values).forEach(function (metric) {
          rows.push(_row(document, [document.run_date, document.proposal_id, mode, metric, values[metric]]));
        });
      });
      return rows;
    },
  },
  monitor: {
    label: "Live monitor snapshot",
    path: "/monitor",
    fields: function (fields, types, aggregations) {
      fields.newDimension().setId("run_date").setName("Run date").setType(types.YEAR_MONTH_DAY);
      fields.newDimension().setId("proposal_id").setName("Proposal").setType(types.TEXT);
      fields.newDimension().setId("ticker").setName("Ticker").setType(types.TEXT);
      fields.newDimension().setId("monitor_updated_at").setName("Monitor updated at").setType(types.TEXT);
      // document.weights holds the reconciled ACTUAL portfolio (STATE/CURRENT
      // or the proposal's actual_weights), never the proposed w_star (N-08).
      fields.newMetric().setId("actual_weight").setName("Actual weight").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("live_weight").setName("Live weight").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("drift_l1").setName("Proposal drift L1").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("live_drift_l1").setName("Live drift L1").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("rebalance_required").setName("Rebalance required").setType(types.BOOLEAN).setAggregation(aggregations.MAX);
      return fields;
    },
    rows: function (document) {
      var rows = [];
      var live = document.live_weights || document.weights || {};
      var actual = document.weights || {};
      var tickers = {};
      Object.keys(actual).forEach(function (ticker) {
        tickers[ticker] = true;
      });
      Object.keys(live).forEach(function (ticker) {
        tickers[ticker] = true;
      });
      Object.keys(tickers).forEach(function (ticker) {
        rows.push(
          _row(document, [
            document.run_date,
            document.proposal_id,
            ticker,
            document.monitor_updated_at || "",
            actual[ticker] === undefined ? null : actual[ticker],
            live[ticker] === undefined ? null : live[ticker],
            document.drift_l1,
            document.live_drift_l1 === undefined ? null : document.live_drift_l1,
            document.rebalance_required,
          ])
        );
      });
      return rows;
    },
  },
  backtest: {
    label: "Weekly backtest comparison",
    path: "/backtest",
    fields: function (fields, types, aggregations) {
      fields.newDimension().setId("run_date").setName("Run date").setType(types.YEAR_MONTH_DAY);
      fields.newDimension().setId("generated_at").setName("Generated at").setType(types.TEXT);
      fields.newDimension().setId("mode").setName("Allocation mode").setType(types.TEXT);
      fields.newMetric().setId("selected_window_days").setName("Selected window (days)").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("in_sample_sharpe").setName("In-sample Sharpe").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("out_of_sample_sharpe").setName("Out-of-sample Sharpe").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("out_of_sample_annualized_return").setName("Out-of-sample annualized return").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("out_of_sample_max_drawdown").setName("Out-of-sample max drawdown").setType(types.NUMBER).setAggregation(aggregations.MAX);
      return fields;
    },
    rows: function (document) {
      var rows = [];
      var comparison = document.comparison || [];
      for (var index = 0; index < comparison.length; index++) {
        var entry = comparison[index];
        rows.push(
          _row(document, [
            document.metadata.run_date,
            document.generated_at,
            entry.mode,
            document.selected.window_days,
            entry.in_sample_sharpe,
            entry.out_of_sample_sharpe,
            entry.out_of_sample_annualized_return,
            entry.out_of_sample_max_drawdown,
          ])
        );
      }
      return rows;
    },
  },
  backtest_candidates: {
    label: "Weekly backtest candidates",
    path: "/backtest",
    fields: function (fields, types, aggregations) {
      fields.newDimension().setId("run_date").setName("Run date").setType(types.YEAR_MONTH_DAY);
      fields.newDimension().setId("selected").setName("Selected").setType(types.BOOLEAN);
      fields.newMetric().setId("gamma").setName("Gamma").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("transaction_cost_lambda").setName("Transaction cost lambda").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("alpha").setName("Return shrinkage alpha").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("window_days").setName("Window (days)").setType(types.NUMBER).setAggregation(aggregations.MAX);
      fields.newMetric().setId("validation_sharpe").setName("Validation Sharpe").setType(types.NUMBER).setAggregation(aggregations.MAX);
      return fields;
    },
    rows: function (document) {
      var rows = [];
      var candidates = document.candidates || [];
      for (var index = 0; index < candidates.length; index++) {
        var candidate = candidates[index];
        rows.push(
          _row(document, [
            document.metadata.run_date,
            candidate.gamma === document.selected.gamma &&
            candidate.transaction_cost_lambda === document.selected.transaction_cost_lambda &&
            candidate.alpha === document.selected.alpha &&
            candidate.window_days === document.selected.window_days,
            candidate.gamma,
            candidate.transaction_cost_lambda,
            candidate.alpha,
            candidate.window_days,
            candidate.validation_sharpe,
          ])
        );
      }
      return rows;
    },
  },
};

function getAuthType() {
  return cc.newAuthTypeResponse().setAuthType(cc.AuthType.OAUTH2).build();
}

function isAdminUser() {
  return false;
}

function isAuthValid() {
  return getOAuthService().hasAccess();
}

function resetAuth() {
  getOAuthService().reset();
}

function get3PAuthorizationUrls() {
  return getOAuthService().getAuthorizationUrl();
}

function authCallback(request) {
  var authorized = getOAuthService().handleCallback(request);
  if (authorized) {
    return HtmlService.createHtmlOutput("Autorizacion completada. Cierra esta pestana y vuelve a Looker Studio.");
  }
  return HtmlService.createHtmlOutput("Autorizacion denegada. Vuelve a intentarlo en Looker Studio.");
}

function getConfig(request) {
  var config = cc.getConfig();
  config.newInfo().setId("instructions").setText("Elige la tabla del portafolio. La autenticacion usa tu usuario Cognito.");
  // Community connector Config API (DataStudioApp): newSelectSingle() takes one
  // addOption() per option; option builders come from config.newOptionBuilder().
  var table = config.newSelectSingle().setId("table").setName("Tabla");
  Object.keys(TABLES).forEach(function (name) {
    table.addOption(config.newOptionBuilder().setLabel(TABLES[name].label).setValue(name));
  });
  config.setDateRangeRequired(false);
  return config.build();
}

function getSchema(request) {
  var table = _table(request);
  var fields = cc.getFields();
  var types = cc.FieldType;
  var aggregations = cc.AggregationType;
  var built = table.fields(fields, types, aggregations);
  return { schema: built.build() };
}

function getData(request) {
  var table = _table(request);
  var document = _fetch(table.path);
  var fields = cc.getFields();
  var types = cc.FieldType;
  var aggregations = cc.AggregationType;
  var built = table.fields(fields, types, aggregations);
  var requested = built.forIds(
    request.fields.map(function (field) {
      return field.name;
    })
  );
  var rows = table.rows(document).map(function (row) {
    return { values: row };
  });
  return { schema: requested.build(), rows: rows };
}

function _table(request) {
  var name = request.configParams && request.configParams.table;
  if (!TABLES[name]) {
    _fail("Selecciona una tabla valida del conector.");
  }
  return TABLES[name];
}

function _fetch(path) {
  var service = getOAuthService();
  if (!service.hasAccess()) {
    _fail("La sesion expiro; vuelve a autorizar el conector.");
  }
  var options = {
    method: "get",
    headers: { Authorization: "Bearer " + service.getAccessToken() },
    muteHttpExceptions: true,
  };
  var response = UrlFetchApp.fetch(_apiEndpoint() + path, options);
  var code = response.getResponseCode();
  if (code === 401) {
    service.reset();
    _fail("El token de acceso fue rechazado; vuelve a autorizar el conector.");
  }
  if (code !== 200) {
    _fail("La API del portafolio respondio " + code + "; revisa el endpoint y reintenta.");
  }
  return JSON.parse(response.getContentText());
}

function _row(document, values) {
  var row = values.slice();
  row[0] = typeof row[0] === "string" ? row[0].replace(/-/g, "") : row[0];
  return row;
}

function _apiEndpoint() {
  var value = PropertiesService.getScriptProperties().getProperty("API_ENDPOINT");
  if (!value) {
    _fail("Configura API_ENDPOINT en las propiedades del script (Apps Script).");
  }
  return String(value).replace(/\/$/, "");
}

function getOAuthService() {
  var props = PropertiesService.getScriptProperties();
  var domain = props.getProperty("COGNITO_DOMAIN");
  var clientId = props.getProperty("CLIENT_ID");
  var clientSecret = props.getProperty("CLIENT_SECRET");
  if (!domain || !clientId || !clientSecret) {
    _fail("Configura COGNITO_DOMAIN, CLIENT_ID y CLIENT_SECRET en las propiedades del script (Apps Script).");
  }
  return (
    OAuth2.createService("portfolioReader")
      .setAuthorizationBaseUrl(String(domain).replace(/\/$/, "") + "/oauth2/authorize")
      .setTokenUrl(String(domain).replace(/\/$/, "") + "/oauth2/token")
      .setClientId(clientId)
      .setClientSecret(clientSecret)
      .setCallbackFunction("authCallback")
      .setPropertyStore(PropertiesService.getUserProperties())
      .setScope("openid email profile")
      .setCache(CacheService.getUserCache())
  );
}

function _fail(message) {
  cc.newUserError().setText(message).throwException();
}
