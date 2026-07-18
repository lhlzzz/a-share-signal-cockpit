module.exports = {
  apps: [
    {
      name: 'xiaogu-cdp',
      script: '/workspace/hermes-workspaces/xiaogu/start_xiaogu_cdp_9333.sh',
      args: '',
      cwd: __dirname,
      interpreter: 'bash',
      autorestart: true,
      max_restarts: 3,
      restart_delay: 5000,
      env: {
        HERMES_PROJECT: 'xiaogu',
      },
    },
  ],
};
