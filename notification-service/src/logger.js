const logger = {
  info:  (obj, msg) => console.log(JSON.stringify({ level: 'INFO',  ...obj, msg })),
  warn:  (obj, msg) => console.warn(JSON.stringify({ level: 'WARN',  ...obj, msg })),
  error: (obj, msg) => console.error(JSON.stringify({ level: 'ERROR', ...obj, msg })),
};
module.exports = logger;
